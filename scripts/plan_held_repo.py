#!/usr/bin/env python3
"""A plan for a held repo names lifting the hold as a step that needs the CEO,
or it is sent back at the first critic (DRE-7752).

On 2026-10-19 the CEO approved DRE-7600, a plan for agent-bureau-demo, a repo
whose reconcile stub had held its WIP cap at "0" since 2026-09-08. The plan's
up-front question named one hand step and not the hold, so the plan sat with
nothing built and nobody told. The planner never read the cap, and neither
critic did. Lifting a hold is a step only the CEO takes, and DRE-7521 says
every such step is in the one question he answers before the work starts.

So the hold is read at planning, through `sweep_holds.read` (DRE-7750) and
nothing else — no second reader of the cap (epic DRE-7671) — and it is
STRUCTURAL, in the seam's shape (`plan_seam_gate.py`, `plan_proof_reach.py`):
one line per unnamed hold, appended to the structural file the gate forwards
into the first critic's result, so the round is a `SEND_BACK` whatever the
model wrote. With no hold on, nothing is appended, and an unheld repo's plan
decides exactly as it did before this module existed.

## What names a hold

A paragraph opening `OPENER` — that line, after an optional list marker or
bold markup, plus its continuation lines up to the next blank line. A max_wip
hold is named when such a paragraph says `max_wip` and quotes the stub's own
comment (both sides whitespace-collapsed), or, when the stub has no comment
about its cap, says `max_wip` and `0`. An Intake hold is named when such a
paragraph says `INTAKE_HOLD`.

## Scope: the plan's own repo, and only that

The holds read are the checkout's — the epic's own `repo:` repo. A child
labeled for another repo is not read: the plan run holds no checkout of it,
and reading its stub would be a second reader of the cap. Every note line
ends with `SCOPE`, so a pass is never read as covering every repo the plan's
cards touch.

CLI (MAX_WIP, BUREAU_MAX_WIP and INTAKE_HOLD read from the environment):
  context [--workspace W]
      The `REPO HOLDS:` block for the planner's assembled context: one line
      when unheld; when held, one line per hold and the paragraph to write.
  findings --artifact F [--workspace W] [--structural-file F] [--note-file F]
      Appends one `held-repo:` line per unnamed hold to the structural file
      (never truncates it), and one `Repo holds:` line to the note file.
      Always exits 0.
"""

from __future__ import annotations

import argparse
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import sweep_holds  # noqa: E402

#: The opener of the paragraph that names a step only the CEO can take.
#: `briefs/planner.md` and `standards/card-quality.md` quote it.
OPENER = "Step that needs the CEO:"

#: Every `Repo holds:` note line ends with this, held or not.
SCOPE = "— read for this plan's own repo only"

#: What each structural line opens with, so the epic's note says which check.
FINDING_PREFIX = "held-repo:"

#: The heading `context` opens with.
CONTEXT_HEADING = "REPO HOLDS:"

#: What the note line opens with.
NOTE_PREFIX = "Repo holds:"

#: The opener at the start of a line, after an optional blockquote, list
#: marker and bold or italic markup — on either side of the colon.
_OPENER_LINE = re.compile(
    r"^[ \t]*(?:>[ \t]*)*(?:(?:[-*+]|\d+[.)])[ \t]+)?(?:\*\*|__|\*|_)?"
    r"Step that needs the CEO(?:\*\*|__|\*|_)?:"
)
#: `0` as a number of its own — never the 0 in 2026-09-08 or in 10.
_ZERO = re.compile(r"(?<![\w.-])0(?![\w-]|\.\d)")


def _collapse(text: str) -> str:
    return " ".join(str(text).split())


def paragraphs(text: str) -> list[str]:
    """Every paragraph opening `OPENER`, whitespace-collapsed, in order.

    A paragraph is the opener line and its continuation lines up to the next
    blank line; a second opener line starts a paragraph of its own."""
    found: list[list[str]] = []
    current: list[str] | None = None
    for line in (text or "").splitlines():
        if _OPENER_LINE.match(line):
            current = [line]
            found.append(current)
        elif not line.strip():
            current = None
        elif current is not None:
            current.append(re.sub(r"^[ \t]*(?:>[ \t]*)*", "", line))
    return [_collapse(" ".join(p)) for p in found]


def names_max_wip(paras: list[str], holds: sweep_holds.SweepHolds) -> bool:
    quote = _collapse(holds.stub_comment)
    for para in paras:
        if "max_wip" not in para:
            continue
        if quote and quote in para:
            return True
        if not quote and _ZERO.search(para):
            return True
    return False


def names_intake(paras: list[str]) -> bool:
    return any("INTAKE_HOLD" in para for para in paras)


def _intake_value(holds: sweep_holds.SweepHolds) -> str:
    if holds.intake_hold:
        return f'INTAKE_HOLD "{holds.intake_hold}"'
    return "INTAKE_HOLD set with no date"


def _max_wip_where(holds: sweep_holds.SweepHolds) -> str:
    """Where the cap is held, and the stub's words for why."""
    if holds.stub_path is None:
        return f"from {holds.cap_source}, with no stub line to quote"
    if not holds.stub_comment:
        return f"in {holds.stub_path}, which carries no comment about it"
    return f'in {holds.stub_path}, whose comment says: "{holds.stub_comment}"'


def _how_to_name_max_wip(holds: sweep_holds.SweepHolds) -> str:
    if holds.stub_comment:
        return "names max_wip and quotes the stub's comment"
    return "names max_wip and 0"


# --- findings ---------------------------------------------------------------

def findings(holds: sweep_holds.SweepHolds, artifact_text: str | None,
             unread: str = "") -> list[str]:
    """One `held-repo:` line per hold that is on and the plan does not name.

    `artifact_text` None is an artifact that could not be read, and `unread`
    says why: while a hold is on, an unread plan is never a pass."""
    paras = paragraphs(artifact_text) if artifact_text is not None else []
    lines = []
    if holds.max_wip_held and (artifact_text is None or not names_max_wip(paras, holds)):
        lines.append(_finding(
            f'max_wip "0" {_max_wip_where(holds)}', artifact_text, unread,
            f"lifting it ({_how_to_name_max_wip(holds)})"))
    if holds.intake_held and (artifact_text is None or not names_intake(paras)):
        lines.append(_finding(
            f"{_intake_value(holds)} — Intake is closed", artifact_text, unread,
            "lifting it (names INTAKE_HOLD)"))
    return lines


def _finding(held: str, artifact_text: str | None, unread: str, step: str) -> str:
    if artifact_text is None:
        why = (f"the plan artifact could not be read ({unread}), and an unread "
               "plan is never a pass")
    else:
        why = (f'the plan names no "{OPENER}" paragraph for {step}; write one '
               "under ## Risk assessment and repeat it in the plan comment")
    return _collapse(f"{FINDING_PREFIX} this plan's repo is held — {held} — {why}.")


def note_line(holds: sweep_holds.SweepHolds, artifact_text: str | None) -> str:
    """The one `Repo holds:` line the epic's note carries, held or not."""
    if not holds.held:
        return f"{NOTE_PREFIX} none — max_wip {holds.cap} ({holds.cap_source}); Intake open {SCOPE}"
    paras = paragraphs(artifact_text) if artifact_text is not None else []

    def _verdict(named: bool) -> str:
        if artifact_text is None:
            return "the plan artifact could not be read"
        return "named as a step that needs the CEO" if named else "NOT named as a step that needs the CEO"

    parts = []
    if holds.max_wip_held:
        parts.append(f'max_wip "0" ({holds.cap_source}) — '
                     f"{_verdict(names_max_wip(paras, holds))}")
    else:
        parts.append(f"max_wip {holds.cap} ({holds.cap_source})")
    if holds.intake_held:
        parts.append(f"{_intake_value(holds)} — {_verdict(names_intake(paras))}")
    else:
        parts.append("Intake open")
    return _collapse(f"{NOTE_PREFIX} {'; '.join(parts)} {SCOPE}")


# --- context ----------------------------------------------------------------

def paragraph(holds: sweep_holds.SweepHolds) -> str:
    """The ready-to-use paragraph that names every hold that is on."""
    said = []
    if holds.max_wip_held:
        if holds.stub_path is None:
            said.append(f'The sweep reads this repo\'s max_wip as "0", from '
                        f"{holds.cap_source}.")
        elif holds.stub_comment:
            said.append(f'Its reconcile stub {holds.stub_path} holds max_wip at '
                        f'"0", and the stub\'s own comment says: '
                        f'"{holds.stub_comment}"')
        else:
            said.append(f'Its reconcile stub {holds.stub_path} holds max_wip at '
                        f'"0", with no comment saying why.')
    if holds.intake_held:
        when = f'"{holds.intake_hold}"' if holds.intake_hold else "on, with no date"
        said.append(f"INTAKE_HOLD is set to {when}, so no new card leaves Intake.")
    what = " and ".join(
        w for w, on in (("its max_wip hold", holds.max_wip_held),
                        ("its Intake hold", holds.intake_held)) if on)
    return _collapse(
        f"{OPENER} lifting this repo's hold — {what}. {' '.join(said)} Nothing "
        "this plan files is built until the CEO lifts it, either for this "
        "plan's cards only or in general.")


def context(holds: sweep_holds.SweepHolds) -> str:
    if not holds.held:
        return (f"{CONTEXT_HEADING} none — max_wip {holds.cap} "
                f"({holds.cap_source}); Intake open")
    out = [f"{CONTEXT_HEADING} this plan's repo is held, and lifting each hold "
           f"is a step that needs the CEO {SCOPE}"]
    if holds.max_wip_held:
        out.append(f'- max_wip "0" {_max_wip_where(holds)} (cap read from '
                   f"{holds.cap_source})")
    if holds.intake_held:
        out.append(f"- {_intake_value(holds)} — Intake is closed")
    out += [
        "",
        "Write this paragraph under ## Risk assessment in the plan artifact, "
        "and repeat it in the plan comment. Without it the first critic sends "
        "the plan back:",
        "",
        paragraph(holds),
    ]
    return "\n".join(out)


# --- CLI --------------------------------------------------------------------

def _holds(workspace: str | None) -> sweep_holds.SweepHolds:
    return sweep_holds.read(
        workspace,
        os.environ.get("MAX_WIP"),
        os.environ.get("BUREAU_MAX_WIP"),
        os.environ.get("INTAKE_HOLD"),
    )


def _append(path: str | None, lines: list[str], what: str) -> None:
    """Appended, never written: the seam and the proof reach write the same
    structural file and the gate reads them all."""
    if not path or not lines:
        return
    try:
        with open(path, "a", encoding="utf-8") as fh:
            for line in lines:
                fh.write(line + "\n")
    except OSError as exc:
        print(f"plan held repo: could not append to the {what} {path}: {exc}",
              file=sys.stderr)


def _append_note(path: str | None, line: str) -> None:
    if not path:
        return
    try:
        lead = "\n" if os.path.isfile(path) and os.path.getsize(path) else ""
    except OSError:
        lead = ""
    _append(path, [lead + line], "note file")


def _cmd_context(args) -> int:
    print(context(_holds(args.workspace)))
    return 0


def _cmd_findings(args) -> int:
    holds = _holds(args.workspace)
    unread = ""
    try:
        with open(args.artifact, encoding="utf-8") as fh:
            artifact_text: str | None = fh.read()
    except (OSError, UnicodeDecodeError) as exc:
        artifact_text, unread = None, f"{args.artifact}: {exc}"
    lines = findings(holds, artifact_text, unread)
    _append(args.structural_file, lines, "structural file")
    note = note_line(holds, artifact_text)
    _append_note(args.note_file, note)
    print(note)
    for line in lines:
        print(line)
    return 0


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    workspace = {"default": os.environ.get("GITHUB_WORKSPACE"),
                 "help": "the plan's own checkout (default: $GITHUB_WORKSPACE)"}

    c = sub.add_parser("context", help="the REPO HOLDS: block for the planner")
    c.add_argument("--workspace", **workspace)
    c.set_defaults(fn=_cmd_context)

    f = sub.add_parser("findings", help="held-repo: lines for every unnamed hold")
    f.add_argument("--artifact", required=True, help="the plan artifact to read")
    f.add_argument("--workspace", **workspace)
    f.add_argument("--structural-file", default=None,
                   help="the first critic's structural file, appended to")
    f.add_argument("--note-file", default=None,
                   help="the mechanical note, appended one Repo holds: line")
    f.set_defaults(fn=_cmd_findings)

    args = ap.parse_args(argv)
    return args.fn(args)


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
