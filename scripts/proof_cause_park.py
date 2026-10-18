#!/usr/bin/env python3
"""One writer holds a proof card on its cause rows for the operator (DRE-6612, DRE-6837).

A record's cause rows — DRE-6607's `Not observed. cause: <cause-word> — <what
the run saw instead>` — are met by two readers: the proof run that wrote them,
at the end of its own run, and the proof dispatcher, when it reads the record
at its head. A re-run would read the same thing again, so the card is asked
ONE question, addressed to the operator, whichever reader meets the rows
first. The CEO decided on 2026-10-07 (DRE-6244) that the operator, never the
CEO, writes an accepted row, so the question is the operator's and the card
stays in the lane it was read in — `In Review`, where its open record pull
request already puts it, on every path the epic builds. Nothing here moves a
card to any lane (DRE-6837).

## What it writes, in order

  1. **The hold** — `linear_ops.cmd_proof_waiting(card, observed, needs)` from
     `hold(rows)`: the criteria cut to 70, and `the operator's decision on <n>
     row(s) the run cannot observe as worded (cause: <words>)`, whose opening
     is `CAUSE_HOLD_OPENING`. Neither half ever names `proof_record.CEO_PRESS`,
     so `proof_dispatch._answered_after_park` never reads a cause park as a
     press park. Skipped when the NEWEST cause hold on the thread
     (`is_cause_hold`) already opens with the same `proof_waiting_line`: an
     amendment to different cause rows is held, and asked, again.
  2. **The question** — `question(rows)`, posted with `linear_ops.cmd_comment`:
     `QUESTION_OPENING`, the three lines `console_escalation.render` writes,
     what the operator does next (`NEXT_STEP`, `MIXED_SHAPE`), one `cause:`
     line per row and the run. Skipped when a comment opening
     `QUESTION_OPENING` — or the opening it had before DRE-6837 — already
     follows that hold. A thread too full to take it is refused, and says so.

Then `park` returns what it did and the lane the card was READ in, never a
lane assumed: `left <CARD> in <lane> for the operator` (`result_line`). The
caller hands the lane in — the dispatcher its card's, the CLI `--lane` or the
card read off Linear — because two paths can reach the writer with the card
somewhere other than `In Review`: the proof run's shell when its own advance
was refused, and the dispatcher's Green Light read under the sweep's
review-cap park.

## Which reader asks

The proof dispatcher's order is DRE-6814's and is unchanged: in `rerunning`,
`_waiting` runs before `_caused`, and `_caused` reaches `park` only when every
unmet row carries a cause word (DRE-6772's shape). A record with rows waiting
on an event beside its cause rows (DRE-6694's) is held by the dispatcher for
the event and asked nothing by it; its cause question is asked by the proof
run's own result step (DRE-6840), off the escalation file the run writes
whenever its record carries a cause row.

## What discharges a cause hold

The operator's amendment, read off the record. The hold is a receipt for the
operator and a clock for the sweep's alarm (DRE-6852), never a hold on the
dispatcher: for a card in `In Review` whose record was read at its head, the
dispatcher drops every hold `is_cause_hold` reads and lets the record decide —
still all cause rows, and `park` asks again on any new rows or posts nothing
on the same ones; any other unmet row, and the re-run re-observes every row.
A `🔬 proof-observed` still discharges it as `proof_record.open_holds` reads
it, which is the operator's move only for the mixed shape (`MIXED_SHAPE`).

## The cause line

`cause: <cause-word> — “<criterion cut to 70>” — <what was seen instead>`, one
per row, in the question and in the proof run's escalation file
(`cause_line`, read back by `rows_from_lines`). The cause words come only from
`proof_reach.causes()` (DRE-6605); the record row's grammar only from
`proof_record.CAUSE_OPENING` (DRE-6607).

A LEAF, like `proof_record`: nothing here imports `reconcile`, which reads
`os.environ["REPO"]` when it is imported.

CLI — `park` writes the hold and the question and moves no card; the rest
write nothing:

    python3 scripts/proof_cause_park.py park <CARD> (--escalation-file F | --record F) [--run-url U] [--lane L] [--dry-run]
    python3 scripts/proof_cause_park.py hold --escalation-file F
    python3 scripts/proof_cause_park.py question --escalation-file F [--run-url U] [--recommendation R]
    python3 scripts/proof_cause_park.py rows --record F

`park` reads the rows off the escalation file's `cause:` lines, with the
file's own 💡 Recommendation, or off the record's cause rows. Without
`--lane` it reads the card's lane off Linear, in the dry run too — a read is
not a write. `--dry-run` prints `would:` lines — the hold line, the question
body, and last `would: leave <CARD> in <lane> for the operator` — and writes
nothing. Live, its last line is the result line, so a shell can quote it.
A file with no cause row makes each exit 2 saying so; a cause line the module
cannot read, or a refused write, makes `park` exit 1.
"""

from __future__ import annotations

import argparse
import re
import sys
from collections import namedtuple
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import console_escalation  # noqa: E402 — the three lines, spelled once
import linear_ops  # noqa: E402 — the hold line and the three writers
import proof_reach  # noqa: E402 — the cause words, spelled once
import proof_record  # noqa: E402 — the record's rows, read as the gate reads them
import spoken_thread  # noqa: E402 — the thread `park` reads

#: The question's first words, read by the siblings to find a standing ask.
#: 🙋 is the glyph `reconcile._AGENT_COMMENT_PREFIXES` and
#: `limit_recovery.RECEIPT_GLYPHS` read as the pipeline's: the comment carries
#: no act trailer, so the glyph is the only mark that says the pipeline wrote it.
QUESTION_OPENING = "🙋 For the operator — the proof run cannot observe"
#: The opening the question had before DRE-6837, still read as an ask.
_EARLIER_OPENING = "🙋 The proof run cannot observe"
#: The hold's `needs` half opens with it: the park's own hold, the one the
#: dispatcher reads past and the sweep's alarm (DRE-6852) keeps time on.
CAUSE_HOLD_OPENING = "the operator's decision on"
#: The one question the operator answers.
QUESTION = "Accept each row as out of date, reword it on a new card, or drop it?"
#: Why a Recommendation that gave no reason is still recommended.
RECOMMENDED_BY = "recommended by the proof run that wrote the rows"
#: Why no recommendation was given.
NONE_WHY = ("the proof run states no recommendation; which rows to accept, "
            "reword or drop is the operator's decision")

#: What the operator does next for a cause row. Each of the first two is a new
#: head of the record pull request; it never says the CEO is asked.
NEXT_STEP = (
    "What the operator does next: accept a row on the record as "
    "`ACCEPTED by operator decision — <why> (<link to the decision>)` "
    "(DRE-6244's linked-decision form), or reword it on a new card and amend "
    "the record to say so, or drop the record.",
    "Accepting or rewording is a new head of the record pull request, which "
    "the critic and the merge gate read, and that new head is what moves the "
    "card.",
)
#: The one place `🔬 proof-observed` is the operator's move: a record whose
#: cause rows stand beside rows waiting on an event. For cause rows alone the
#: dispatcher reads the same record and refuses again.
MIXED_SHAPE = ("If the record also carries rows waiting on an event, record "
               "🔬 proof-observed once the event has happened, so the "
               "dispatcher re-runs the proof over every row.")
#: What a criterion's `proof_record.CEO_PRESS` is written as, in the hold and
#: in the proof dispatcher's hold for the operator's press: no one's press.
PRESS_NAMED = "the press named on the card"

CRITERION_CUT = 70
CAUSE_PREFIX = "cause:"

#: One cause row: the criterion, its cause word, and what the run saw instead
#: (empty when the row said nothing after the word).
CauseRow = namedtuple("CauseRow", "criterion cause seen")

#: `cause: <word> — “<criterion>” — <seen>`.
_CAUSE_LINE = re.compile(r"cause:\s*(\S+)\s+—\s+“(.*?)”(?:\s+—\s*(.*))?")

#: What the hold may not say, each with the words it says instead:
#: `proof_record.CEO_PRESS` would read as a press park, and
#: `linear_ops.CONSOLE_HOLD_MARKERS` as a fix-budget hold.
_REWORDED = ((proof_record.CEO_PRESS, PRESS_NAMED),
             ("budget exhausted", "budget spent"),
             ("holding for a human", "waiting on a person"))


def _flat(text) -> str:
    return " ".join(str(text or "").split())


def _criterion(row: CauseRow) -> str:
    return proof_record._cut(row.criterion, CRITERION_CUT)


def _plain(text: str) -> str:
    """`text` with nothing a later reader takes for a mark of its own."""
    for said, instead in _REWORDED:
        text = re.sub(re.escape(said), instead, text, flags=re.IGNORECASE)
    return text


def _seen_after(result: str, word: str) -> str:
    """The text after the first `—` that follows the cause word, flattened."""
    at = result.lower().find("cause:")
    at = result.find(word, at)
    dash = result.find("—", at + len(word)) if at >= 0 else -1
    return _flat(result[dash + 1:]) if dash >= 0 else ""


def rows_of(record_text: str) -> list:
    """Every unmet row of the record whose result carries a cause word."""
    rows = []
    for criterion, result in proof_record.reading(record_text).unmet:
        word = proof_record.row_cause(result)
        if word:
            rows.append(CauseRow(criterion, word, _seen_after(result, word)))
    return rows


def rows_from_lines(lines) -> list:
    """The rows the escalation file's `cause:` lines name. A line naming a word
    outside `proof_reach.causes()`, or not in the grammar, is refused."""
    four = proof_reach.causes()
    rows = []
    for line in lines:
        text = line.strip()
        if not text.startswith(CAUSE_PREFIX):
            continue
        found = _CAUSE_LINE.fullmatch(text)
        if found is None:
            raise ValueError(f"the line “{_flat(text)}” is not "
                             f"`cause: <word> — “<criterion>” — <what was seen>`")
        word = found.group(1)
        if word not in four:
            raise ValueError(f"the cause word “{word}” is not one of the four: "
                             f"{', '.join(four)}")
        rows.append(CauseRow(found.group(2), word, _flat(found.group(3))))
    return rows


def cause_line(row: CauseRow) -> str:
    """The row as one line of the question and the escalation file."""
    return f"cause: {row.cause} — “{_criterion(row)}” — {_flat(row.seen)}".rstrip()


def _words(rows) -> list:
    return list(dict.fromkeys(r.cause for r in rows))


def hold(rows) -> tuple:
    """`(observed, needs)` — the two halves of the `🔬 proof-waiting` hold."""
    observed = "; ".join(_criterion(r) for r in rows)
    needs = (f"the operator's decision on {len(rows)} row(s) the run cannot "
             f"observe as worded (cause: {', '.join(_words(rows))})")
    return _plain(observed), _plain(needs)


def question(rows, *, recommendation=None, run_url=None) -> str:
    """The comment body: the opening, the three lines, a `cause:` line per row
    and the run."""
    seen = "; ".join(f"“{_criterion(r)}” — the run saw {_flat(r.seen) or 'nothing recorded'}"
                     for r in rows)
    finding = (f"{len(rows)} row(s) of the proof record cannot be observed as "
               f"worded (cause: {', '.join(_words(rows))}): {seen}")
    if recommendation is None:
        answer, why = None, NONE_WHY
    else:
        head, _, why = str(recommendation).partition(console_escalation.SEPARATOR)
        answer, why = head.strip(), why.strip() or RECOMMENDED_BY
    lines = console_escalation.render(console_escalation.Escalation(
        finding=finding, question=QUESTION, recommendation=answer, why=why))
    body = (f"{QUESTION_OPENING} {len(rows)} row(s) as worded, and asks:\n\n"
            f"{lines}\n\n" + "\n".join(NEXT_STEP) + f"\n{MIXED_SHAPE}\n\n"
            + "\n".join(cause_line(r) for r in rows))
    return f"{body}\n\nRun: {run_url}" if run_url else body


def _first_line(body) -> str:
    return (body or "").strip().split("\n", 1)[0].strip()


def is_cause_hold(line: str) -> bool:
    """Is `line`, a comment's first line, the park's own hold — a
    `🔬 proof-waiting` whose `needs` half opens `CAUSE_HOLD_OPENING`?"""
    return (line.startswith(proof_record.HOLD_MARK)
            and f" — needs {CAUSE_HOLD_OPENING} " in line)


def result_line(identifier: str, lane: str) -> str:
    """What `park` says of the card: the lane it was read in, never a move."""
    return f"left {identifier} in {lane} for the operator"


def _asked(body) -> bool:
    text = (body or "").lstrip()
    return text.startswith(QUESTION_OPENING) or text.startswith(_EARLIER_OPENING)


def park(identifier: str, rows, *, voices: list[spoken_thread.Voice], lane: str,
         live: bool = True, recommendation=None, run_url=None) -> str:
    """The hold and the question for cause rows, and the lane the card was
    read in. It writes no lane. See the module docstring."""
    if not rows:
        return "nothing to do — the record carries no cause row"
    if not lane:
        raise ValueError(f"{identifier}: no lane was read for the card, and "
                         "none is assumed")
    observed, needs = hold(rows)
    line = linear_ops.proof_waiting_line(observed, needs)
    causes = [i for i, v in enumerate(voices) if is_cause_hold(_first_line(v.body))]
    held = bool(causes) and _first_line(voices[causes[-1]].body).startswith(line)
    asked = held and any(_asked(v.body) for v in voices[causes[-1] + 1:])
    body = question(rows, recommendation=recommendation, run_url=run_url)
    if not live:
        if not held:
            print(f"would: hold {identifier} — {line}")
        if not asked:
            print(f"would: ask {identifier} — {body}")
        return f"would: leave {identifier} in {lane} for the operator"
    if not held:
        linear_ops.cmd_proof_waiting(identifier, observed, needs)
    if not asked and linear_ops.cmd_comment(identifier, body) == \
            linear_ops.COMMENT_CAP_CONDITION:
        raise linear_ops.LinearError(
            f"{identifier}: the thread is full — the question to the operator "
            "could not be posted")
    done = "already asked" if asked else "asked" if held else "held, asked"
    return f"{done}, {result_line(identifier, lane)}"


# --------------------------------------------------------------------------- #
# the CLI                                                                      #
# --------------------------------------------------------------------------- #


class _NoRows(Exception):
    pass


def _read(path: str) -> str:
    return Path(path).read_text(encoding="utf-8")


def _file_rows(path: str) -> list:
    rows = rows_from_lines(_read(path).splitlines())
    if not rows:
        raise _NoRows(f"{path} carries no `{CAUSE_PREFIX}` line")
    return rows


def _cmd_hold(args) -> None:
    print("\t".join(hold(_file_rows(args.escalation_file))))


def _cmd_question(args) -> None:
    print(question(_file_rows(args.escalation_file), recommendation=args.recommendation,
                   run_url=args.run_url))


def _park_rows(args) -> tuple:
    """`(rows, recommendation)` off the escalation file or the record."""
    if args.record:
        rows = rows_of(_read(args.record))
        if not rows:
            raise _NoRows(f"{args.record} carries no `Not observed. {CAUSE_PREFIX}` "
                          "row naming one of the four cause words")
        return rows, None
    rows = _file_rows(args.escalation_file)
    text = _read(args.escalation_file)
    said = console_escalation.parse(text)
    if said is None or said.recommendation is None:
        return rows, None
    # The line's own value, as the shipped step hands it to `question`.
    prefix = console_escalation.RECOMMENDATION_PREFIX
    return rows, next(x.strip()[len(prefix):].strip() for x in text.split("\n")
                      if x.strip().startswith(prefix))


def _cmd_park(args) -> None:
    rows, recommendation = _park_rows(args)
    lane = args.lane or (linear_ops.get_issue(args.card, fresh=True).get("state")
                         or {}).get("name")
    voices: list = []
    if not args.dry_run:
        voices = linear_ops.read_card_voices(args.card)
        if voices is None:
            raise linear_ops.LinearError(f"{args.card}'s thread could not be read, "
                                         "so no hold or question can be ruled out")
    said = park(args.card, rows, voices=voices, lane=lane, live=not args.dry_run,
                recommendation=recommendation, run_url=args.run_url)
    result = result_line(args.card, lane)
    if said.endswith(f", {result}"):
        print(f"proof_cause_park: {args.card} — {said[:-len(result) - 2]}")
        said = result
    print(said)


def _cmd_rows(args) -> None:
    rows = rows_of(_read(args.record))
    if not rows:
        raise _NoRows(f"{args.record} carries no `Not observed. {CAUSE_PREFIX}` row "
                      "naming one of the four cause words")
    for row in rows:
        print(cause_line(row))


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="cmd", required=True)
    cmd = sub.add_parser("park", help="hold the card and ask the operator; "
                                      "moves no card")
    cmd.add_argument("card")
    source = cmd.add_mutually_exclusive_group(required=True)
    source.add_argument("--escalation-file")
    source.add_argument("--record")
    cmd.add_argument("--run-url")
    cmd.add_argument("--lane", help="the card's lane, when the caller read it")
    cmd.add_argument("--dry-run", action="store_true")
    cmd.set_defaults(fn=_cmd_park)
    cmd = sub.add_parser("hold", help="print <observed>\\t<needs>")
    cmd.add_argument("--escalation-file", required=True)
    cmd.set_defaults(fn=_cmd_hold)
    cmd = sub.add_parser("question", help="print the question's comment body")
    cmd.add_argument("--escalation-file", required=True)
    cmd.add_argument("--run-url")
    cmd.add_argument("--recommendation")
    cmd.set_defaults(fn=_cmd_question)
    cmd = sub.add_parser("rows", help="print the cause lines a record's rows make")
    cmd.add_argument("--record", required=True)
    cmd.set_defaults(fn=_cmd_rows)
    args = parser.parse_args(argv)
    try:
        args.fn(args)
    except _NoRows as said:
        print(f"proof_cause_park: {said}", file=sys.stderr)
        return 2
    except (OSError, ValueError, linear_ops.LinearError) as said:
        print(f"proof_cause_park: {said}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
