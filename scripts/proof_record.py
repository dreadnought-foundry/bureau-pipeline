#!/usr/bin/env python3
"""The proof record's readers, shared by the merge gate and the PROOF close
(DRE-6141).

On 2026-10-07 three proof records whose criterion rows read `Not observed.`
merged on green CI and the critic's APPROVE, and DRE-5919's close-on-merge
marked their cards Done; DRE-5798 closed four minutes after its run posted a
`🔬 proof-waiting` hold naming the CEO's press. Neither half opened the record
or read the hold. Both now do, through this module:

1. **The proof-record branch rule** — `agent/DRE-<n>-proof-record`
   (`proof_record_branch`), moved from `proof_dispatch`.
2. **The criterion-table reader** — `criterion_rows`, `is_closing_row`,
   `row_met`, `reading` and `_row`, moved from `hygiene_done`: the one shape a
   machine can read without judgment. Every row but the record's own merge and
   the CEO's closing step (`is_closing_row`: one of `CLOSING_ROW_WORDS`, in the
   shape of a merge to main the criterion leads with, or of the CEO or the
   operator closing the card or reading the record) must open with one of
   `MET_WORDS`, no hedge straight after it (`HEDGES`).
3. **The hold-discharge reader** — `open_holds`, moved from `proof_dispatch`.
4. **The record finder**, new: the ONE `.md` file the pull request ADDS under
   `docs/` or `architecture/` (`find_record`, over `gh pr view --json files`),
   read at a given sha through the contents API (`fetch`). No match, or two, is
   no record — never a fixed path.

`shortfall` is the one judgment both halves make over a record. `gather` is the
merge gate's feed: `python3 proof_record.py gather` writes the record the gate
reads with `--proof-record-file`, the same shape as `stacked_prs.py gather`.

A LEAF, on purpose: `reconcile` reads `os.environ["REPO"]` when it is imported,
and neither the merge gate's `Evaluate and merge` step nor linear-sync's
`Card → Done` step sets `REPO`. This module imports nothing that imports
`reconcile` or `hygiene`, and `tests/test_proof_record.py` imports it with
`REPO` unset.
"""

from __future__ import annotations

import argparse
import base64
import binascii
import json
import re
import subprocess  # nosec B404 — one fixed argv, `gh`, no shell
import sys
from collections import namedtuple
from pathlib import Path
from urllib.parse import quote

sys.path.insert(0, str(Path(__file__).resolve().parent))
import linear_ops  # noqa: E402 — the proof line's mark and tags, spelled once
import spoken_thread  # noqa: E402 — who said each comment, signature checked

# --------------------------------------------------------------------------- #
# the proof-record branch                                                      #
# --------------------------------------------------------------------------- #

_RECORD_BRANCH = re.compile(r"agent/DRE-\d+-proof-record")


def proof_record_branch(head_ref) -> bool:
    """Is `head_ref` a proof run's record branch, `agent/DRE-<n>-proof-record`?
    The fix-agent card, the re-run card and the merge gate read it."""
    return bool(_RECORD_BRANCH.fullmatch(head_ref or ""))


# --------------------------------------------------------------------------- #
# the criterion table                                                          #
# --------------------------------------------------------------------------- #

#: A criterion row naming one of these is the record's own merge or the CEO's
#: closing step, and is skipped — but only in one of the two shapes below, so
#: "Retry closes the loop" or "The nightly runs on main" is still judged.
CLOSING_ROW_WORDS = ("merged", "on main", "the ceo", "close")
#: A result cell opening with one of these, as a whole word, is met — unless a
#: hedge follows straight after it ("Met, but only…", "Pass with caveats").
MET_WORDS = ("met", "holds", "observed", "proven", "pass", "yes")
HEDGES = ("but", "only", "except", "partly", "partially", "with caveats?", "and no", "and not",
          "not")

_FENCE = re.compile(r"^\s*(```|~~~)")
_SEPARATOR_CELL = re.compile(r":?-+:?")
_UNESCAPED_PIPE = re.compile(r"(?<!\\)\|")
_MET = re.compile(rf"(?:{'|'.join(MET_WORDS)})\b")
_HEDGED = re.compile(rf"(?:{'|'.join(MET_WORDS)})\b[\s,;:—–-]*(?:{'|'.join(HEDGES)})\b")
#: The record's own merge: the criterion leads with it — "Merged to main…",
#: "The record is on main", "docs/<record>.md merged to main".
_OWN_MERGE = re.compile(r"(?:merged to main|(?:the |this )?record (?:is )?(?:merged to|on) main"
                        r"|\S+\.md (?:is )?(?:merged to|on) main)\b")
#: The closing step: the CEO or the operator closes the card, or reads the record.
_CLOSING_STEP = re.compile(r"\bthe (?:ceo|operator)\b[^.;:]*?\b(?:clos(?:es|ed|e) (?:this card"
                           r"|the card|it)|reads? (?:the|this) (?:merged )?record)\b")

#: `rows` is None when the record holds no criterion table; `met` and `unmet`
#: are the (criterion, result) pairs that are not closing rows.
Reading = namedtuple("Reading", "rows met unmet")


def _cells(line: str) -> list:
    row = line.strip()
    if row.startswith("|"):
        row = row[1:]
    if row.endswith("|") and not row.endswith("\\|"):
        row = row[:-1]
    return [c.strip().replace("\\|", "|") for c in _UNESCAPED_PIPE.split(row)]


def _is_row(line: str) -> bool:
    return line.lstrip().startswith("|")


def _is_separator(line: str) -> bool:
    return _is_row(line) and all(_SEPARATOR_CELL.fullmatch(c) for c in _cells(line))


def _cell(row: list, at: int) -> str:
    return row[at] if at < len(row) else ""


def criterion_rows(text: str) -> list | None:
    """The first markdown table whose header row has a cell containing
    `criteri`, as (criterion cell, the cell after it) pairs — or None when the
    record holds no such table. Tables inside fenced code are not tables."""
    lines = (text or "").splitlines()
    fenced = False
    i = 0
    while i < len(lines):
        line = lines[i]
        if _FENCE.match(line):
            fenced = not fenced
            i += 1
            continue
        if fenced or not _is_row(line) or i + 1 >= len(lines) or not _is_separator(lines[i + 1]):
            i += 1
            continue
        header = _cells(line)
        body, i = [], i + 2
        while i < len(lines) and _is_row(lines[i]):
            body.append(_cells(lines[i]))
            i += 1
        at = next((k for k, c in enumerate(header) if "criteri" in c.lower()), None)
        if at is not None:
            return [(_cell(r, at), _cell(r, at + 1)) for r in body]
    return None


def is_closing_row(criterion: str) -> bool:
    text = " ".join((criterion or "").replace("`", "").lower().split())
    if not any(word in text for word in CLOSING_ROW_WORDS):
        return False
    return _OWN_MERGE.match(text) is not None or _CLOSING_STEP.search(text) is not None


def _plain(cell: str) -> str:
    return (cell or "").replace("*", "").replace("_", "").strip()


def row_met(result: str) -> bool:
    text = _plain(result).lower()
    return _MET.match(text) is not None and _HEDGED.match(text) is None


def reading(text: str) -> Reading:
    rows = criterion_rows(text)
    judged = [r for r in rows or [] if not is_closing_row(r[0])]
    return Reading(rows=rows, met=[r for r in judged if row_met(r[1])],
                   unmet=[r for r in judged if not row_met(r[1])])


def _row(criterion: str, result: str) -> str:
    def cut(text: str, n: int) -> str:
        text = " ".join(text.split())
        return text if len(text) <= n else text[:n - 1].rstrip() + "…"
    return f"“{cut(criterion, 70)}” reads “{cut(result.replace('*', ''), 50)}”"


# --------------------------------------------------------------------------- #
# the card's holds                                                             #
# --------------------------------------------------------------------------- #

#: What a hold names when only the CEO's own login can discharge it (DRE-5925).
CEO_PRESS = "the CEO's press"
HOLD_MARK = f"{linear_ops.PROOF_MARK} {linear_ops.PROOF_WAITING_TAG}:"
OBSERVED_MARK = f"{linear_ops.PROOF_MARK} {linear_ops.PROOF_OBSERVED_MARK}:"


def _first_line(text: str | None) -> str:
    return (text or "").strip().split("\n", 1)[0].strip()


def open_holds(voices: list) -> list:
    """The `🔬 proof-waiting` holds nothing later on the thread discharged.

    Anyone's hold holds — a copy can only keep a card waiting. A later
    `🔬 proof-observed` from the pipeline's key or a person discharges the
    operator's holds; only his signed answer discharges one naming the CEO's
    press. An unsigned claim to be his answer discharges nothing, and nor does
    one whose signature could not be checked (DRE-4153)."""
    held: list = []
    for voice in voices:
        body = (voice.body or "").lstrip()
        if body.startswith(HOLD_MARK):
            held.append(_first_line(body))
        elif voice.kind == spoken_thread.UNCHECKED:
            continue  # neither his answer nor a refused one: it discharges nothing
        elif voice.kind == spoken_thread.CEO_VIA_CONSOLE:
            held = [h for h in held if CEO_PRESS not in h]
        elif (body.startswith(OBSERVED_MARK)
              and voice.kind in (spoken_thread.PIPELINE, spoken_thread.PERSON)):
            held = [h for h in held if CEO_PRESS in h]
    return held


# --------------------------------------------------------------------------- #
# the record                                                                   #
# --------------------------------------------------------------------------- #

#: Where a proof run writes its record: one new `.md` file under either — the
#: roots the hygiene lane reads a merged record from (`hygiene_done.RECORD_ROOTS`
#: is this tuple). Narrower would hold most records: bureau-pipeline keeps its
#: proofs under `docs/` (#772 added `docs/claude-limit-recovery-proof-2026-10.md`),
#: agent-bureau under `architecture/proofs/`, and nothing re-runs a held record.
RECORD_DIRS = ("docs/", "architecture/")
ADDED = "ADDED"

#: The record a pull request carries. `text` is None when there is none to
#: judge — none found, or none readable — and `detail` then says why.
Record = namedtuple("Record", "path text detail")


def _one_line(text, limit: int = 300) -> str:
    text = " ".join(str(text or "").split())
    return text if len(text) <= limit else text[:limit - 1].rstrip() + "…"


def find_record(files) -> tuple:
    """`(path, None)` for the single `.md` file the pull request ADDS under
    `RECORD_DIRS`, or `(None, why)` — no such file, two or more, or a file
    list that cannot be read. `files` is `gh pr view --json files`'s list;
    each entry carries `path` and `changeType`."""
    if not isinstance(files, list):
        return None, "the pull request's file list could not be read"
    entries = [f for f in files if isinstance(f, dict) and isinstance(f.get("path"), str)]
    if len(entries) != len(files):
        return None, "the pull request's file list could not be read"
    under = [f for f in entries if f["path"].startswith(RECORD_DIRS) and f["path"].endswith(".md")]
    added = [f["path"] for f in under if f.get("changeType") == ADDED]
    if len(added) == 1:
        return added[0], None
    if len(added) > 1:
        return None, (f"the pull request adds {len(added)} records — {', '.join(added)} — "
                      "and a proof record is one file")
    others = ", ".join(f"{f['path']} ({str(f.get('changeType') or 'unknown').lower()})"
                       for f in under)
    return None, ("the pull request adds no .md file under " + " or ".join(RECORD_DIRS)
                  + (f" — it touches {others}" if others else ""))


def _gh(args: list) -> str:
    """`gh <args>`'s stdout, or RuntimeError — never a guessed answer."""
    try:
        done = subprocess.run(["gh", *args], capture_output=True, text=True,  # nosec B603 B607
                              check=False, timeout=60)
    except (OSError, subprocess.SubprocessError) as exc:
        raise RuntimeError(f"{type(exc).__name__}: {exc}") from exc
    if done.returncode != 0:
        raise RuntimeError((done.stderr or done.stdout or f"gh exited {done.returncode}").strip())
    return done.stdout


def record_text(repo: str, path: str, ref: str, gh=None) -> str:
    """The file's text at `ref`, through the contents API — the call
    `hygiene_done._record_text` makes at the default branch. Raises on a read
    that fails or answers without the content."""
    doc = json.loads((gh or _gh)(["api", f"repos/{repo}/contents/{quote(path)}?ref={ref}"])
                     or "null") or {}
    if not isinstance(doc, dict) or doc.get("encoding") != "base64":
        raise ValueError(f"{path} came back without its content")
    return base64.b64decode(doc.get("content") or "").decode("utf-8")


def fetch(repo: str, files, ref: str, gh=None) -> Record:
    """The pull request's record, read at `ref`. Never raises: a record not
    found, or not read, is a `Record` with no text and the reason."""
    path, why = find_record(files)
    if path is None:
        return Record(None, None, why)
    try:
        return Record(path, record_text(repo, path, ref, gh), None)
    except (RuntimeError, ValueError, TypeError, binascii.Error, UnicodeDecodeError) as exc:
        return Record(path, None, f"{path} could not be read at {ref}: {_one_line(exc)}")


def shortfall(record: Record) -> str | None:
    """Why this record does not prove its card, or None when every judged row
    is met — the one judgment the merge gate and the PROOF close make."""
    if record is None or record.text is None:
        detail = getattr(record, "detail", None) or "nothing was read"
        return f"no proof record could be read — {_one_line(detail)}"
    path = record.path or "the record"
    read = reading(record.text)
    if read.rows is None:
        return f"{path} has no criterion table"
    if read.unmet:
        rows = "; ".join(_row(c, r) for c, r in read.unmet)
        return f"{path} has {len(read.unmet)} row(s) not met: {rows}"
    if not read.met:
        return f"{path} has a criterion table with no row but its closing step"
    return None


# --------------------------------------------------------------------------- #
# the merge gate's feed                                                        #
# --------------------------------------------------------------------------- #


def gather(view, repo: str, sha: str, gh=None) -> dict:
    """The record the merge gate is handed with `--proof-record-file`. Off a
    proof-record branch the rule does not apply and nothing is read."""
    branch = view.get("headRefName") if isinstance(view, dict) else None
    if not proof_record_branch(branch):
        return {"applies": False, "branch": branch}
    found = fetch(repo, view.get("files"), sha, gh)
    return {"applies": True, "path": found.path, "text": found.text, "detail": found.detail}


def read_payload(payload) -> Record | None:
    """Parse what `gather` wrote: None when the rule does not apply, else the
    record — and anything that is not provably a record is one with no text,
    which the gate holds on."""
    if isinstance(payload, dict) and payload.get("applies") is False:
        return None
    if not isinstance(payload, dict) or payload.get("applies") is not True:
        return Record(None, None, "the proof record file is not a record")
    path, text = payload.get("path"), payload.get("text")
    if text is not None and not isinstance(text, str):
        return Record(None, None, "the proof record file carries no text")
    if text is None:
        return Record(path if isinstance(path, str) else None, None,
                      str(payload.get("detail") or "the record was not read"))
    return Record(path if isinstance(path, str) else None, text, None)


def _cmd_gather(args) -> int:
    try:
        with open(args.pr_view_file, encoding="utf-8") as fh:
            view = json.load(fh)
    except (OSError, ValueError) as exc:
        view = None
        print(f"proof_record: the pull request record could not be read: {exc}",
              file=sys.stderr)
    if view is None:
        # Nothing says which branch this is, so nothing says the rule is off:
        # the gate checks the branch itself and holds a proof record on this.
        record = {"applies": True, "path": None, "text": None,
                  "detail": "the pull request record could not be read"}
    else:
        record = gather(view, args.repo, args.head_sha)
    with open(args.out, "w", encoding="utf-8") as fh:
        json.dump(record, fh)
    if not record["applies"]:
        print("proof_record: not a proof-record branch — the rule does not apply")
    elif record["text"] is None:
        print(f"proof_record: no record to judge — {_one_line(record['detail'])}")
    else:
        print(f"proof_record: read {record['path']} at {args.head_sha}")
    return 0


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="cmd", required=True)
    cmd = sub.add_parser("gather", help="write the merge gate's proof record")
    cmd.add_argument("--repo", required=True)
    cmd.add_argument("--pr-view-file", required=True,
                     help="the gate's one `gh pr view` read — its headRefName "
                          "and files[] say whether and where there is a record")
    cmd.add_argument("--head-sha", required=True,
                     help="the evaluated head: the record is read there")
    cmd.add_argument("--out", required=True)
    cmd.set_defaults(fn=_cmd_gather)
    args = parser.parse_args(argv)
    return args.fn(args)


if __name__ == "__main__":
    sys.exit(main())
