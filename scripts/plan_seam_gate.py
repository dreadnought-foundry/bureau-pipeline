#!/usr/bin/env python3
"""The seam gate — the one mechanical finding that is a decision (DRE-3398).

Every other finding `plan_critic.py mechanical` prints is INPUT: the list lands
on the epic, the critic reads it, and the critic decides. That is the right
shape for advice and it was not enough for this one. On DRE-3164 two critics
read a fifteen-card plan across six rounds and never named the seam it carried —
three cards that could not start until a person had watched DRE-3166 run live —
so the plan the CEO approved was two epics wearing one number, and everything
past the seam was work nobody had costed.

So this finding is STRUCTURAL. It is written into the critic's result file
BEFORE `plan_critic.py decide` reads it, and the round becomes a `SEND_BACK`
with the seam as its reason whatever the model wrote. `decide` is not edited and
does not know this module exists: it reads the rewritten file through the
grammar it already has (`read_result`, `further_findings`, `all_findings`), and
the marker it writes is whatever it writes for any send-back.

## What this module knows about seams: nothing

It forwards lines. The sentence is `scripts/plan_seam.py`'s (DRE-3395), the
structural file is that module's output, and this one reads it as plain text.
Neither module is edited by the other's card, and the only import here is
`plan_critic` — the grammar the result file is written in.

## The three answers

* **No structural file — REFUSE (exit 1).** A mechanical step that never ran
  must not read as "no seam". The two facts are different and only one of them
  clears a plan (`standards/console-honesty.md` rule 1); an absent file is the
  first, and a check that cannot tell them apart is worth nothing on the day the
  step breaks.
* **An empty structural file — do nothing, exit 0.** The check ran and found no
  seam. The result file is left BYTE-IDENTICAL, so a pass decides exactly as it
  did before this module existed.
* **Lines — rewrite.** On a `PASS` or a `NO_RESULT` the first line becomes ours,
  carrying `PREFIX` and the first seam line. On a critic `SEND_BACK` the first
  line stays the critic's — it found the worst gap and the seam joins its list
  rather than displacing it.

Either way the critic's own text is kept, indented four spaces: a further
finding is a numbered line at COLUMN ZERO (`plan_critic._FURTHER_FINDING`), so
indenting keeps the critic's working out of this round's findings, and the FIRST
result line wins in `read_result`, so its own header below ours decides nothing.

CLI:
  gate --result-file F --structural-file F
      Exit 0 and the file is either rewritten or untouched. Exit 1 and the
      structural file could not be read, with the reason on stderr.
"""

from __future__ import annotations

import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import plan_critic  # noqa: E402

#: The attribution the reason carries, so the epic's note says where the finding
#: came from — the check, not the model. ONE constant: the workflow does not
#: spell it, the test derives what it expects from it, and it is the only string
#: in this module that reaches a human.
PREFIX = "The mechanical check found this before the critic read the plan: "

#: How far the critic's own text is pushed right. Four, because that is what
#: takes a numbered line out of `further_findings` and puts it in the working.
INDENT = "    "


def structural_lines(path: str) -> list[str]:
    """The seam findings, one per line, or raise.

    Plain text on purpose. The structural file is a contract of one sentence per
    line and nothing else, so a reader that needed a schema would be a second
    place the two cards could disagree.

    Blank lines are dropped: the writer ends each line with a newline and an
    empty file is legitimately zero findings, so a trailing newline must not
    become a finding with no text in it.
    """
    with open(path, encoding="utf-8") as fh:
        return [line.strip() for line in fh if line.strip()]


def rewrite(result_text: str, lines: list[str]) -> str:
    """The result file the decision step should read, given the seam lines.

    Never called with an empty `lines`: no seam is not a rewrite, it is a file
    left alone, and that decision belongs to the caller so the byte-identical
    guarantee is visible in one place.
    """
    result, reason = plan_critic.read_result(result_text)
    if result == plan_critic.SEND_BACK:
        # The critic found the worst gap itself. Keep its sentence as the
        # headline and put the seam at the top of the list, ahead of the rest of
        # what it found — the re-plan reads the list in order.
        head = plan_critic.result_line(plan_critic.SEND_BACK, reason)
        items = lines + [f for f in plan_critic.further_findings(result_text)
                         if f not in lines]
    else:
        # A PASS, or a round that wrote no verdict at all. Both are rounds that
        # did not stop a two-epic plan, and neither is a reason to let it past.
        #
        # The first seam line is listed in the form it takes as the headline, so
        # `all_findings` reads ONE finding rather than the same sentence twice —
        # the note the CEO reads would otherwise say "Every finding this round
        # (2)" about a single seam, which is not noise but a wrong number.
        items = [PREFIX + lines[0]] + lines[1:]
        head = plan_critic.result_line(plan_critic.SEND_BACK, items[0])
    body = "\n".join(INDENT + line if line.strip() else line
                     for line in (result_text or "").splitlines())
    return "\n\n".join(part for part in
                       [head, plan_critic.findings_block(items), body.rstrip()]
                       if part) + "\n"


def _cmd_gate(args) -> int:
    try:
        lines = structural_lines(args.structural_file)
    except OSError as exc:
        # Fail CLOSED, and say which file. The plan run dies here rather than
        # deciding a round on a seam check whose output nobody can find.
        print(f"plan seam gate: could not read the structural file "
              f"{args.structural_file} ({exc}) — a seam check that never ran "
              "is not the same fact as no seam, so this round does not decide",
              file=sys.stderr)
        return 1
    if not lines:
        print("no seam — the result file is unchanged")
        return 0
    try:
        with open(args.result_file, encoding="utf-8") as fh:
            result_text = fh.read()
    except OSError:
        # A critic that wrote nothing is `NO_RESULT`, which is a rewrite like
        # any other — the seam decides the round the crash left undecided.
        result_text = ""
    rewritten = rewrite(result_text, lines)
    with open(args.result_file, "w", encoding="utf-8") as fh:
        fh.write(rewritten)
    result, reason = plan_critic.read_result(rewritten)
    print(f"seam: {len(lines)} finding(s) written into "
          f"{args.result_file} — {result}: {reason}")
    return 0


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    sub = ap.add_subparsers(dest="cmd", required=True)

    g = sub.add_parser("gate",
                       help="write the seam into the critic's result file")
    g.add_argument("--result-file", required=True,
                   help="the critic's result file, rewritten in place")
    g.add_argument("--structural-file", required=True,
                   help="the seam findings, one per line (scripts/plan_seam.py)")
    g.set_defaults(fn=_cmd_gate)

    args = ap.parse_args(argv)
    return args.fn(args)


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
