#!/usr/bin/env python3
"""The CEO's verified console answers, written into the card (DRE-6357).

On DRE-3879 he named the two branches in a signed answer at 13:12 PT; the
critic read the card at 13:16 PT, saw no branch names, and sent the card back
to him; a person hand-edited the card at 13:18 PT. On 2026-10-08 the operator
made that edit by hand five times on three cards. This module is that edit,
made by the planning run: the decision becomes part of the CARD, not a comment
the card's readers never look at.

## What is copied

Only the comments `spoken_thread.voices` labels `ceo-via-console` — the
signature was checked. "Answer from Sid" typed into Linear, a pipeline comment
claiming to be him, a receipt that was refused and one that could not be
checked are not his, and none of them is copied. For each answer the words are
`console_receipt.answer_text` with the console's own heading taken off
(`words`), headed by the signed time in PT and quoted, oldest first, under
`HEADING`.

## Where it goes

The block runs from the `HEADING` line to the next line starting `## `, or to
the end of the text. It replaces a previous block in place, or is appended to
the end when there is none. Every line inside it is the heading, the
provenance sentence, a bold time line or a quoted line, so nothing inside can
read as the block's end, as a heading, or as an acceptance criterion. With the
key readable and no verified answer, a block is REMOVED: a hand-typed one has
no signed answer behind it, and the planner's brief tells the classifier the
words under that heading were signed. Everything outside the block is left
byte for byte, and a second run over the same thread changes nothing.

## What it never carries

The signature. The block has no trailer, no `sha256=` and no `sig=`, and its
first line says so: the comment holds the signature, this is a copy. Nothing
in a description is proof of identity.

## The CLI

`write <CARD>` is run by the planning run (DRE-6360) and by nothing else. It
reads the whole thread the way `green_light_reply.py` does, and the whole
description the way `linear_ops.py description` does — never the list API,
which truncates descriptions silently. It writes only when the text changed,
prints one `answer-into-card:` line, and exits 0 whatever happened: a failed
read or write is a printed line, never a red planning run, because its caller
goes on to post the revision comment and dispatch the re-read under `bash -e`.
`--github-output F` hands the description as it stands after the step to the
next step as `description`, through `github_output.render`. The value is
Linear text and untrusted: the caller feeds it to the sanitizer and nowhere
else.
"""

from __future__ import annotations

import argparse
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import console_receipt  # noqa: E402 — ONE reader of a console-signed receipt
import github_output  # noqa: E402 — ONE writer of a step output
import linear_ops  # noqa: E402 — ONE Linear client
import spoken_thread  # noqa: E402 — ONE reader of who said what

HEADING = "## Decisions from the CEO"
#: Opens the CLI's one status line.
PREFIX = "answer-into-card:"
#: The console's heading above his words (`console_receipt.ANSWER_SPEC`) —
#: the same pattern `spoken_thread.newest_answer` strips, pinned equal by a
#: test so the two readers cannot drift.
ANSWER_HEAD = re.compile(r"^Answer from .* PT:$")
#: The block's first line, under the heading.
PROVENANCE = ("Copied from his signed console answers; the signature stays on "
              "the comment and this copy proves nothing on its own.")

#: (signed_at_iso, words)
Answer = tuple[str, str]

HELP = ("It always exits 0: a card with no answer is the common case, and a "
        "failed read or write is a printed line, never a red planning run — "
        "the step that runs this goes on to post the revision comment and "
        "dispatch the re-read.")


def words(answer_text: str) -> str:
    """His words: `answer_text` with the console's heading line taken off when
    the first line, stripped, is one, and the line after it too only when that
    line is blank. Nothing else is read or removed."""
    lines = answer_text.split("\n")
    if not ANSWER_HEAD.match(lines[0].strip()):
        return answer_text
    rest = lines[1:]
    if rest and not rest[0].strip():
        rest = rest[1:]
    return "\n".join(rest)


def _block(answers: list[Answer]) -> list[str]:
    out = [HEADING, PROVENANCE]
    for signed_at, said in sorted(answers, key=lambda answer: answer[0]):
        out += ["", f"**{spoken_thread.pacific_label(signed_at)}**", ""]
        out += [f"> {line}" if line.strip() else ">"
                for line in said.strip("\n").split("\n")]
    return out


def transcribe(description: str | None, answers: list[Answer]) -> str:
    """`description` with the block for `answers` in place of the previous
    one, or appended when there was none; with no answers, the block removed.
    Everything outside the block is returned as it was."""
    text = description or ""
    block = _block(answers) if answers else []
    lines = text.split("\n")
    out: list[str] = []
    placed = False
    i = 0
    while i < len(lines):
        if lines[i].rstrip() != HEADING:
            out.append(lines[i])
            i += 1
            continue
        # A block: up to the next `## ` line, or the end. A second hand-typed
        # block under the same heading is removed, so exactly one remains.
        i += 1
        while i < len(lines) and not lines[i].startswith("## "):
            i += 1
        if block and not placed:
            out += [*block, ""]
        placed = True
    if placed or not block:
        return "\n".join(out)
    if not text:
        sep = ""
    elif text.endswith("\n\n"):
        sep = ""
    elif text.endswith("\n"):
        sep = "\n"
    else:
        sep = "\n\n"
    return text + sep + "\n".join(block) + "\n"


# --------------------------------------------------------------------------- #
# the CLI                                                                      #
# --------------------------------------------------------------------------- #

def _read_description(card: str) -> str:
    """The whole description, off the single-issue read `linear_ops.py
    description` makes — never the list API, which truncates it."""
    data = linear_ops.gql(
        """query($id: String!) { issue(id: $id) { description } }""",
        {"id": card},
    )
    issue = (data or {}).get("issue")
    if issue is None:
        raise LookupError(f"Linear returned no issue {card}")
    return issue.get("description") or ""


def _why(exc: BaseException) -> str:
    return f"{type(exc).__name__}: {exc}"


def write(card: str, *, dry_run: bool = False) -> tuple[str, str | None]:
    """(what it did, the description as it stands after the step). The
    description is None when the thread or the card could not be read."""
    try:
        nodes, viewer = linear_ops._thread_and_viewer(
            card, "body", "user", "createdAt", whole=True)
        current = _read_description(card)
    except Exception as exc:  # noqa: BLE001 — a printed line, never a red run
        return f"could not check: could not read {card} — {_why(exc)}", None
    try:
        heard = spoken_thread.voices(nodes, viewer, card=card)
    except Exception as exc:  # noqa: BLE001
        return f"could not check: {_why(exc)}", current
    unchecked = [v for v in heard if v.kind == spoken_thread.UNCHECKED]
    if unchecked:
        return f"could not check: {unchecked[0].why}", current
    answers = []
    for voice in heard:
        if voice.kind != spoken_thread.CEO_VIA_CONSOLE:
            continue
        receipt = console_receipt.parse_answer(voice.body)
        said = words(console_receipt.answer_text(voice.body))
        if receipt is not None and said.strip():
            answers.append((receipt.at, said))
    rendered = transcribe(current, answers)
    if rendered == current:
        return ("unchanged" if answers
                else "no verified answer on this card"), current
    did = (f"{len(answers)} answer(s) into {card}'s description" if answers
           else f"0 answer(s) — removed a {HEADING} block from {card} with no "
                "verified answer behind it")
    if dry_run:
        return f"dry run, nothing written — would write {did}", rendered
    try:
        linear_ops.set_description(card, rendered)
    except Exception as exc:  # noqa: BLE001 — the caller runs under bash -e
        return f"could not write: {_why(exc)}", current
    return f"wrote {did}", rendered


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="answer_into_card.py",
        description="Write the CEO's verified console answers into the card's "
                    f"description under '{HEADING}'. {HELP}")
    sub = parser.add_subparsers(dest="command", required=True)
    cmd = sub.add_parser("write", help="transcribe one card's answers",
                         description=HELP)
    cmd.add_argument("card")
    cmd.add_argument("--dry-run", action="store_true",
                     help="print the status line, then the description it "
                          "would write; write nothing")
    cmd.add_argument("--github-output", metavar="F",
                     help="append the step output `description` to F")
    args = parser.parse_args(argv)
    card = args.card.strip()
    with github_output.only_outputs():
        did, after = write(card, dry_run=args.dry_run)
    print(f"{PREFIX} {did}")
    if args.dry_run and after is not None:
        sys.stdout.write(after if after.endswith("\n") else after + "\n")
    if args.github_output and after is not None:
        try:
            with open(args.github_output, "a", encoding="utf-8") as fh:
                fh.write(github_output.render([("description", after)]))
        except OSError as exc:
            print(f"{PREFIX} could not append the step output: {_why(exc)}",
                  file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
