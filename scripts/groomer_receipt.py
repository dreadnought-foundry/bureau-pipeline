#!/usr/bin/env python3
"""The groomer's model receipt, read off `proposal.json` (DRE-3153).

One line, posted to the card a judged `propose` proposed on:

    🧠 model-attempt: <receipt> — groomer judgement ranked the census in 1 call

It opens with the marker `plan.yml` posts for the planning classifier, because
the ladder's readers — the console, the stranded-run watchdog, the model-drift
report — find a model attempt by that marker and a second grammar would be a
second answer to the same question.

## Why this is a Python file and not three lines of YAML

`plan.yml` learned it the expensive way (DRE-3083): a receipt assembled in a
shell string is a second answer to "which model answered", and nothing tests a
shell string. So the line is composed here, over the proposal the run already
wrote, and the workflow does two things with the result — post it and print it.

## What it refuses to invent

`judgement.output_budget` and `judgement.truncated` are DRE-3259's keys. A
`proposal.json` written before that card carries NEITHER, and this reader is
run against whatever the checked-out `groomer.py` produced. An absent key is
**not reported**: the clause is omitted and no number is guessed. A budget of
`0` is the same thing said differently — that is what `groomer._annotate`
writes when no call was made — so it is omitted rather than printed as a
budget nothing was asked with.

## When there is nothing to post

`judgement.enabled` false (`--no-judgement`) or `calls` 0 (no model was ever
asked — an unpicked model, a prompt that would not compose). No model attempt
happened, so a model-attempt receipt would name one that did not. The reader
writes an empty file and the workflow step posts nothing.

Nothing here raises: a missing or unreadable `proposal.json` is a run that died
before it proposed, which the step summary already shows. A receipt step that
fails the job would turn a missing footnote into a red run over a proposal that
is sitting on the card exactly as intended.

CLI:

    python3 scripts/groomer_receipt.py --proposal proposal.json --out receipt.txt
"""

from __future__ import annotations

import argparse
import json
import sys

#: The marker every model attempt in this pipeline opens with.
MARKER = "🧠 model-attempt:"


def _plural(count: int, noun: str) -> str:
    return f"{count} {noun}" + ("" if count == 1 else "s")


def receipt_line(proposal: dict) -> str | None:
    """The one line a judged run posts, or None when there is nothing to say."""
    block = (proposal or {}).get("judgement") or {}
    if not block.get("enabled"):
        return None
    calls = int(block.get("calls") or 0)
    if calls < 1:
        return None

    line = (f"{MARKER} {block.get('receipt')} — groomer judgement ranked the "
            f"census in {_plural(calls, 'call')}")

    # The count (DRE-3331), read when the proposal carries it: `answered` on
    # its own was the whole receipt of a run that ranked 56 cards of 260.
    if "ranked" in block and proposal.get("population") is not None:
        line += (f" — {int(block.get('ranked') or 0)} of "
                 f"{_plural(int(proposal['population']), 'card')} ranked")

    # DRE-3259's two keys, read when present and never inferred from the other.
    budget = int(block.get("output_budget") or 0)
    if budget > 0:
        line += f", output budget {budget} tokens"
    if block.get("truncated"):
        unranked = len(block.get("unranked") or [])
        line += (f" — answer cut short; {_plural(unranked, 'card')} unranked "
                 "for it")
    return line


def read(path: str) -> dict:
    """The proposal, or an empty one. Never raises — see the module docstring."""
    try:
        with open(path, encoding="utf-8") as fh:
            loaded = json.load(fh)
    except (OSError, ValueError) as e:
        print(f"groomer receipt: no proposal to read at {path}: {e}",
              file=sys.stderr)
        return {}
    return loaded if isinstance(loaded, dict) else {}


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--proposal", default="proposal.json",
                        help="the proposal JSON a `propose` run wrote")
    parser.add_argument("--out",
                        help="write the line here — EMPTY when there is "
                             "nothing to post, so the caller can test it with "
                             "`[ -s ]` and never has to tell 'no receipt' "
                             "from 'the reader crashed'")
    args = parser.parse_args(argv)

    line = receipt_line(read(args.proposal)) or ""
    if args.out:
        with open(args.out, "w", encoding="utf-8") as fh:
            fh.write(line)
    elif line:
        print(line)
    return 0


if __name__ == "__main__":                                  # pragma: no cover
    sys.exit(main())
