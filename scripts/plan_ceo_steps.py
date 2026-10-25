#!/usr/bin/env python3
"""A plan whose child needs the CEO names that child in its up-front question,
or it is sent back at the first critic (DRE-7523).

DRE-7521 put the rule in `standards/card-quality.md`: every step that needs the
CEO — his sign-in, a press from his own session, a production apply he must OK,
a name only he can give — is listed in the one question he answers before the
work starts. Portico's portals release stood stuck for more than ten hours on
2026-10-17/18 (DRE-7494) on a hand step the proof session (DRE-7235) found two
laps in. DRE-7752 checked one such step, a held repo's hold
(`plan_held_repo.py`); this module reads the rest off the plan's own children.

It is STRUCTURAL, in the held-repo check's shape: one line per unnamed child,
appended to the structural file the seam gate forwards into the first critic's
result, so the round is a `SEND_BACK` whatever the model wrote. A plan with no
child that needs the CEO appends nothing, and decides exactly as it did before
this module existed.

## A child that needs the CEO

Open (`plan_critic.shipped_work_is_a_finding`), and either:

* an operator card by `plan_proof_reach.is_operator_card` — the hold label, the
  `operator-step` mark, or routed `OPERATOR` by its labels or title, PROOF
  titles excluded. The step is the card's title.
* carrying an acceptance criterion (`plan_proof_reach.criteria`) on a person's
  row (`plan_proof_reach.PERSON_ROWS`). This is how a PROOF card's operator
  steps are read. The step is each such criterion's text.

## Named

When a paragraph opening `plan_held_repo.OPENER`, as `plan_held_repo.paragraphs`
reads them, names the child's identifier as a whole token — `DRE-1234`, never
the `DRE-1234` inside `DRE-12345`.

CLI:
  findings --artifact F --detail-file D [--structural-file S] [--note-file N]
      `children-json` records on stdin, `children-detail` records in D — the
      two reads `plan_proof_reach.py findings` takes. Appends one `ceo-step:`
      line per unnamed child to S (never truncates it), and one `CEO steps:`
      line, with the findings under it, to N. Always exits 0.
"""

from __future__ import annotations

import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import plan_critic  # noqa: E402
import plan_held_repo  # noqa: E402
import plan_proof_reach  # noqa: E402
import plan_seam  # noqa: E402

#: What each structural line opens with, so the epic's note says which check.
FINDING_PREFIX = "ceo-step:"

#: What the note line opens with.
NOTE_PREFIX = "CEO steps:"


def _collapse(text: str) -> str:
    return " ".join(str(text).split())


def steps(record: dict, doc: dict | None = None) -> list[str]:
    """The steps that make `record` a child that needs the CEO, or `[]`.

    An operator card's step is its title; any other open child's steps are
    its criteria on a person's row."""
    if not plan_critic.shipped_work_is_a_finding(record):
        return []
    if plan_proof_reach.is_operator_card(record, doc=doc):
        title = _collapse(record.get("title") or "")
        return [f"operator card “{title}”" if title else "an operator card"]
    return [f"criterion “{_collapse(c)}”"
            for c in plan_proof_reach.criteria(record.get("body") or "")
            if plan_proof_reach._person_row(c)]


def needs_the_ceo(records, doc: dict | None = None) -> list[tuple[str, list[str]]]:
    """`(identifier, steps)` for every child that needs the CEO, in order."""
    out = []
    for rec in records or []:
        ident = rec.get("identifier")
        found = steps(rec, doc) if ident else []
        if found:
            out.append((ident, found))
    return out


def named(paras: list[str], ident: str) -> bool:
    return any(plan_proof_reach._names(para, ident) for para in paras)


def findings(cards, details, artifact_text: str | None, unread: str = "",
             doc: dict | None = None) -> list[str]:
    """One `ceo-step:` line per child that needs the CEO and is not named.

    `cards` and `details` are the two board reads `plan_seam.join` merges.
    `artifact_text` None is an artifact that could not be read, and `unread`
    says why: an unread plan is never a pass."""
    paras = plan_held_repo.paragraphs(artifact_text) if artifact_text is not None else []
    return [_finding(ident, found, artifact_text, unread)
            for ident, found in needs_the_ceo(plan_seam.join(cards, details), doc)
            if artifact_text is None or not named(paras, ident)]


def _finding(ident: str, found: list[str], artifact_text: str | None,
             unread: str) -> str:
    what = "; ".join(found)
    if artifact_text is None:
        why = (f"the plan artifact could not be read ({unread}), and an unread "
               "plan is never a pass")
    else:
        why = (f'no "{plan_held_repo.OPENER}" paragraph names {ident}; write '
               "one under ## Risk assessment and repeat it in the plan comment")
    return _collapse(f"{FINDING_PREFIX} {ident} needs the CEO — {what} — {why}.")


def note_line(cards, details, lines: list[str], doc: dict | None = None) -> str:
    """The one `CEO steps:` line the epic's note carries."""
    need = needs_the_ceo(plan_seam.join(cards, details), doc)
    if not need:
        return f"{NOTE_PREFIX} none — no open child is an operator card or carries a person's row"
    unnamed = [ident for ident, _ in need
               if any(line.startswith(f"{FINDING_PREFIX} {ident} ") for line in lines)]
    said = f"{len(need)} child(ren) need the CEO"
    if not unnamed:
        return f"{NOTE_PREFIX} {said} — every one named in a \"{plan_held_repo.OPENER}\" paragraph"
    return (f"{NOTE_PREFIX} {said} — NOT named in a \"{plan_held_repo.OPENER}\" "
            f"paragraph: {', '.join(unnamed)}")


# --- CLI --------------------------------------------------------------------

def _append(path: str | None, lines: list[str], what: str) -> None:
    """Appended, never written: the seam, the proof reach and the held-repo
    check write the same structural file and the gate reads them all."""
    if not path or not lines:
        return
    try:
        with open(path, "a", encoding="utf-8") as fh:
            for line in lines:
                fh.write(line + "\n")
    except OSError as exc:
        print(f"plan ceo steps: could not append to the {what} {path}: {exc}",
              file=sys.stderr)


def _append_note(path: str | None, note: str, lines: list[str]) -> None:
    if not path:
        return
    try:
        lead = "\n" if os.path.isfile(path) and os.path.getsize(path) else ""
    except OSError:
        lead = ""
    _append(path, [lead + note] + [f"- {line}" for line in lines], "note file")


def _cmd_findings(args) -> int:
    cards = plan_proof_reach._records(plan_seam._stdin_json([]))
    details = plan_proof_reach._records(
        plan_proof_reach._json_file(args.detail_file, "the children detail"))
    unread = ""
    try:
        with open(args.artifact, encoding="utf-8") as fh:
            artifact_text: str | None = fh.read()
    except (OSError, UnicodeDecodeError) as exc:
        artifact_text, unread = None, f"{args.artifact}: {exc}"
    try:
        lines = findings(cards, details, artifact_text, unread)
        note = note_line(cards, details, lines)
    except Exception as exc:  # noqa: BLE001 — input to a critic, never a crash
        print(f"plan ceo steps: could not read the plan ({exc!r}) — no finding "
              "is written", file=sys.stderr)
        lines, note = [], f"{NOTE_PREFIX} UNKNOWN — the check could not read the plan ({exc!r})"
    _append(args.structural_file, lines, "structural file")
    _append_note(args.note_file, note, lines)
    print(note)
    for line in lines:
        print(line)
    return 0


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = ap.add_subparsers(dest="cmd", required=True)

    f = sub.add_parser("findings", help="ceo-step: lines for every unnamed child; "
                                        "children-json on stdin")
    f.add_argument("--artifact", required=True, help="the plan artifact to read")
    f.add_argument("--detail-file", required=True,
                   help="children-detail records, for titles and bodies")
    f.add_argument("--structural-file", default=None,
                   help="the first critic's structural file, appended to")
    f.add_argument("--note-file", default=None,
                   help="the mechanical note, appended one CEO steps: line")
    f.set_defaults(fn=_cmd_findings)

    args = ap.parse_args(argv)
    return args.fn(args)


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
