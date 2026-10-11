#!/usr/bin/env python3
"""One writer parks a proof card on its cause rows (DRE-6612).

A record's cause rows — DRE-6607's `Not observed. cause: <cause-word> — <what
the run saw instead>` — are met by two readers: the proof run that wrote them,
at the end of its own run, and the proof dispatcher, when the gate declines
the record at its head and the run did not park the card (the park step's
move was refused, the escalation file was never written, or the record was
amended by hand). A re-run would read the same thing again, so the card is
asked ONE Green Light question, whichever reader meets the rows first. The
hold, the question and the move are composed and written here, and both
readers reach the lane through `park`.

## What it writes, in order

  1. **The hold** — `linear_ops.cmd_proof_waiting(card, observed, needs)` from
     `hold(rows)`: the criteria cut to 70, and `the operator's decision on <n>
     row(s) the run cannot observe as worded (cause: <words>)`. Neither half
     ever names `proof_record.CEO_PRESS`, so `proof_dispatch._answered_after_park`
     never reads a cause park as a press park. Skipped when a comment's first
     line already opens with the same `proof_waiting_line`.
  2. **The question** — `question(rows)`, posted with `linear_ops.cmd_comment`:
     `QUESTION_OPENING`, the three lines `console_escalation.render` writes, one
     `cause:` line per row and the run. Skipped when a comment opening
     `QUESTION_OPENING` already follows the newest such hold. A thread too full
     to take it is refused, and the card is not moved: a card is never carried
     to Green Light with no question on it.
  3. **The move** — `linear_ops.cmd_advance(card, "Green Light", "Hand-work,In
     Review")`, declared on the lane contract as the `agent-escalation` arrival
     `proof_cause_park.py#park`. A refused move raises.

The hold before the question and the question before the move: the order the
lane contract's kind (d) declares.

## The cause line

`cause: <cause-word> — “<criterion cut to 70>” — <what was seen instead>`, one
per row, in the question and in the proof run's escalation file
(`cause_line`, read back by `rows_from_lines`). The cause words come only from
`proof_reach.causes()` (DRE-6605); the record row's grammar only from
`proof_record.CAUSE_OPENING` (DRE-6607).

A LEAF, like `proof_record`: nothing here imports `reconcile`, which reads
`os.environ["REPO"]` when it is imported.

CLI — it writes nothing; no subcommand moves a card:

    python3 scripts/proof_cause_park.py hold --escalation-file F
    python3 scripts/proof_cause_park.py question --escalation-file F [--run-url U] [--recommendation R]
    python3 scripts/proof_cause_park.py rows --record F

A file with no cause row makes each exit 2 saying so.
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
QUESTION_OPENING = "🙋 The proof run cannot observe"
#: The one question the operator answers.
QUESTION = "Accept each row as out of date, reword it on a new card, or drop it?"
#: Why a Recommendation that gave no reason is still recommended.
RECOMMENDED_BY = "recommended by the proof run that wrote the rows"
#: Why no recommendation was given.
NONE_WHY = ("the proof run states no recommendation; which rows to accept, "
            "reword or drop is the operator's decision")

LANE = "Green Light"
FROM_LANES = "Hand-work,In Review"
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
_REWORDED = ((proof_record.CEO_PRESS, "the press only the CEO can make"),
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
            f"{lines}\n\n" + "\n".join(cause_line(r) for r in rows))
    return f"{body}\n\nRun: {run_url}" if run_url else body


def _first_line(body) -> str:
    return (body or "").strip().split("\n", 1)[0].strip()


def park(identifier: str, rows, *, voices: list[spoken_thread.Voice], live: bool = True,
         recommendation=None, run_url=None) -> str:
    """The one Green Light write for cause rows. See the module docstring."""
    if not rows:
        return "nothing to do — the record carries no cause row"
    observed, needs = hold(rows)
    if not live:
        said = f"would: park {identifier} — {needs}"
        print(said)
        return said
    line = linear_ops.proof_waiting_line(observed, needs)
    holds = [i for i, v in enumerate(voices) if _first_line(v.body).startswith(line)]
    asked = bool(holds) and any((v.body or "").lstrip().startswith(QUESTION_OPENING)
                                for v in voices[holds[-1] + 1:])
    if not holds:
        linear_ops.cmd_proof_waiting(identifier, observed, needs)
    if not asked:
        body = question(rows, recommendation=recommendation, run_url=run_url)
        if linear_ops.cmd_comment(identifier, body) == linear_ops.COMMENT_CAP_CONDITION:
            raise linear_ops.LinearError(
                f"{identifier}: the thread is full — the question could not be "
                f"posted, and the card is not moved to {LANE}")
    if not linear_ops.cmd_advance(identifier, "Green Light", "Hand-work,In Review"):
        raise linear_ops.LinearError(
            f"{identifier}: the move to {LANE} out of {FROM_LANES} was refused")
    if asked:
        return "already asked — parked"
    return "asked, parked" if holds else "held, asked, parked"


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
    except (OSError, ValueError) as said:
        print(f"proof_cause_park: {said}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
