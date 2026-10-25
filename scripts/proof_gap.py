#!/usr/bin/env python3
"""The fix card a PROOF closes with (DRE-7952) — one filer, one receipt.

A proof record's GAP rows (`proof_record.gap_rows`) are the unmet rows a
re-run would read the same again: a `Not observed. cause: <word> — …` row
naming one of the four cause words (`config/proof-reach.json` `causes`,
DRE-6607), and a `Not met. history: …` row (DRE-7740). Until DRE-7952 they
held the merge gate and the PROOF close, and the dispatcher put them to the
operator once (`proof_cause_park`, DRE-6837), whose only useful answer was
"reword it on a new card". Now a record whose every unmet row is a gap
closes its PROOF card on its approved merge, and the gaps go where the real
work goes: ONE fix card, filed here.

## The card

`file_fix_card(proof_card, epic, record_pr_url, gaps, *, repo_slug)` creates
it with `linear_ops.create_card(..., lane="Intake")` — parentless, labeled
`repo:<the PROOF's repo>` and nothing else, so the groomer classifies it like
any new work. Its title is `TITLES[kind]`; its body names the PROOF card, the
epic, the record's pull request, and one line per row: the criterion, the
kind, the record's cell and the kind of fix (`FIXES`):

  * `out-of-reach` — a missing reach;
  * `misdescribed` — a reworded criterion or a defect in the build;
  * `state-gone` and `history` — a reworded criterion;
  * `operator-step-unblocked` — an OPERATOR card the proof is blocked by.

`kind="defect"` is the same filer for a spent re-run budget on a build that
fails its rows (the proof dispatcher's card, DRE-7953): the title and every
row name a defect in the build (`DEFECT_FIX`), and a row's own kind is not
read.

## The receipt

`🩹 proof-gap-card: <DRE-n>` (`RECEIPT_MARK`), posted on the PROOF card
through `pipeline_act.receipt` (act `proof-gap-filed`). A pipeline-authored
comment whose first line opens with it is the card's one fix card: a second
close — a re-run of card-done, the reconcile backstop — reads the card off it
and files nothing. A comment of that shape from anyone else counts for
nothing. A receipt that failed to post after its card was created is caught
one step later: an open card with exactly the title is reused and receipted,
never filed again.

A LEAF, like `proof_record`: linear-sync's `Card → Done` step sets no `REPO`,
and nothing here imports `reconcile`.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import linear_ops  # noqa: E402 — the create seam, the thread and the comment writer
import pipeline_act  # noqa: E402 — the receipt's trailer
import proof_reach  # noqa: E402 — the cause words and what each means, spelled once
import proof_record  # noqa: E402 — the gap rows, read as the gate reads them
import spoken_thread  # noqa: E402 — who wrote each comment

#: Opens the receipt's first line, then the fix card's identifier.
RECEIPT_MARK = "🩹 proof-gap-card:"
#: The receipt's act in `config/pipeline-acts.json`.
ACT = "proof-gap-filed"
#: Where the fix card is filed: new work, classified like any other.
LANE = "Intake"

GAP = "gap"
DEFECT = "defect"

#: The kind of fix each gap kind is.
FIXES = {
    "out-of-reach": ("a missing reach — give the proof the reading the row needs, "
                     "then prove the row again"),
    "misdescribed": ("a reworded criterion or a defect in the build — reword the row "
                     "when the product is right, fix the build when it is not"),
    "state-gone": "a reworded criterion — word the row on state the proof can still read",
    proof_record.HISTORY_KIND: ("a reworded criterion — word the row on a window or a run "
                                "the proof can still observe"),
    "operator-step-unblocked": ("an OPERATOR card the proof is blocked by — file the step "
                                "as an OPERATOR card and block the proof on it"),
}
#: The fix every row of a `DEFECT` card names.
DEFECT_FIX = "a defect in the build — fix the build so the row reads met"
#: What a history row is, beside the cause words' own meanings.
HISTORY_MEANING = ("the criterion reads a window that had closed, or a run that had "
                   "finished, before the proof ran")

TITLES = {
    GAP: "{slug}: the gaps PROOF {card} closed with — {k} row(s) not observable as worded",
    DEFECT: "{slug}: the defect in the build PROOF {card} found — {k} row(s) not met",
}

_CUT = 70
_RECEIPT = re.compile(rf"^{re.escape(RECEIPT_MARK)}\s*(DRE-\d+)")
_LINEAR_URL = re.compile(r"https://linear\.app/\S+/issue/DRE-\d+\S*")
_PR_REPO = re.compile(r"github\.com/[\w.-]+/([\w.-]+)/pull/\d+")


def _safe(text) -> str:
    """Untrusted record text, posted by the pipeline: one line, no verdict marker."""
    import merge_gate  # noqa: PLC0415 — lazy: it imports linear_ops, which imports this lazily

    return merge_gate.without_verdict_markers(" ".join(str(text or "").split()))


def meaning(kind: str) -> str:
    """What a gap of this kind is: the cause word's meaning, or a history row's."""
    if kind == proof_record.HISTORY_KIND:
        return HISTORY_MEANING
    return proof_reach.causes().get(kind) or "a row the proof cannot observe as worded"


def gaps_of(read) -> list:
    """`(criterion, kind, cell)` for each of `proof_record.gap_rows(read)` —
    the row's own result cell beside it, in the record's order."""
    rows = iter(read.unmet)
    out = []
    for criterion, kind in proof_record.gap_rows(read):
        for named, cell in rows:
            if named == criterion:
                out.append((criterion, kind, cell))
                break
    return out


def _rows(gaps) -> list:
    """Each gap as `(criterion, kind, cell)` — a pair as `gap_rows` returns
    it carries no cell."""
    out = []
    for gap in gaps:
        criterion, kind, cell = (*tuple(gap), "")[:3]
        out.append((str(criterion or ""), str(kind or ""), str(cell or "")))
    return out


def title(proof_card: str, gaps, *, repo_slug: str, kind: str = GAP) -> str:
    return TITLES[kind].format(slug=repo_slug, card=proof_card, k=len(list(gaps)))


def _line(criterion: str, row_kind: str, cell: str, kind: str) -> str:
    fix = DEFECT_FIX if kind == DEFECT else FIXES[row_kind]
    named = DEFECT if kind == DEFECT else row_kind
    said = f"the record reads “{proof_record._cut(cell, 200)}”" if cell else "no cell was given"
    return (f"- “{proof_record._cut(criterion, _CUT)}” — {named} — {said} — "
            f"the fix: {fix}")


def body(proof_card: str, epic, record_pr_url: str, gaps, *, kind: str = GAP) -> str:
    rows = _rows(gaps)
    if kind == DEFECT:
        opening = (f"The proof of {proof_card} read {len(rows)} row(s) not met on its "
                   f"record, and a re-run reads the same: each is a defect in the build, "
                   "and this card fixes it (DRE-7952).")
    else:
        opening = (f"{proof_card} closed on its approved, merged record with {len(rows)} "
                   "row(s) the proof cannot observe as worded. A re-run would read each the "
                   "same again, so each is a gap, and this card is where the real work goes "
                   "(DRE-7952).")
    lines = [
        opening,
        "",
        f"* PROOF card: {proof_card}",
        f"* Epic: {epic or 'none read'}",
        f"* Record: {record_pr_url}",
        "",
        "## The rows",
        "",
        *(_line(c, k, cell, kind) for c, k, cell in rows),
    ]
    return _safe_lines(lines)


def _safe_lines(lines) -> str:
    return "\n".join(_safe(line) if line.strip() else "" for line in lines)


def filed(voices) -> dict | None:
    """The fix card a pipeline-authored `RECEIPT_MARK` line names, or None."""
    for voice in voices or ():
        if voice.kind != spoken_thread.PIPELINE:
            continue
        first = (voice.body or "").strip().split("\n", 1)[0].strip()
        found = _RECEIPT.match(first)
        if found:
            url = _LINEAR_URL.search(voice.body or "")
            return {"identifier": found.group(1), "url": url.group(0) if url else None}
    return None


def receipt_text(proof_card: str, fix: dict, gaps, *, kind: str = GAP) -> str:
    """The receipt's body, before the trailer."""
    rows = _rows(gaps)
    what = ("the defect in the build its proof found is" if kind == DEFECT
            else "the gaps it closed with are")
    where = f": {fix['url']}" if fix.get("url") else ""
    named = "; ".join(f"“{proof_record._cut(c, _CUT)}” ({DEFECT if kind == DEFECT else k})"
                      for c, k, _ in rows)
    return _safe_lines([
        f"{RECEIPT_MARK} {fix['identifier']} — {what} filed in {LANE}{where}",
        "",
        f"{len(rows)} row(s): {named}.",
    ])


def file_fix_card(proof_card: str, epic, record_pr_url: str, gaps, *, repo_slug: str,
                  kind: str = GAP, voices=None) -> dict:
    """The fix card for `gaps`, created — or the one already filed, read off
    the PROOF card's receipt. `voices` is the card's thread as
    `linear_ops.read_card_voices` reads it, read here when not passed; a
    thread that cannot be read is a refusal, since a filed card cannot be
    ruled out. See the module docstring."""
    if kind not in TITLES:
        raise ValueError(f"the fix card kind {kind!r} is not one of {', '.join(TITLES)}")
    rows = _rows(gaps)
    if not rows:
        raise ValueError(f"{proof_card}: no row to file a fix card for")
    if kind == GAP:
        unknown = sorted({k for _, k, _ in rows if k not in FIXES})
        if unknown:
            raise ValueError(f"{proof_card}: the gap kind(s) {', '.join(unknown)} are not "
                             f"one of {', '.join(FIXES)}")
    if voices is None:
        voices = linear_ops.read_card_voices(proof_card)
        if voices is None:
            raise linear_ops.LinearError(f"{proof_card}'s thread could not be read, so a "
                                         "fix card already filed cannot be ruled out")
    already = filed(voices)
    if already is not None:
        print(f"proof-gap: {proof_card} already filed {already['identifier']} — nothing filed")
        return already
    named = title(proof_card, rows, repo_slug=repo_slug, kind=kind)
    found = linear_ops.find_open(named)
    if found:
        fix = {"identifier": found, "url": None}
        print(f"proof-gap: {found} is already open as {named!r} — receipting it")
    else:
        fix = linear_ops.create_card(named, body(proof_card, epic, record_pr_url, rows, kind=kind),
                                     repo_slug=repo_slug, lane=LANE)
    problem = linear_ops.cmd_comment(
        proof_card, pipeline_act.receipt(ACT, receipt_text(proof_card, fix, rows, kind=kind)))
    if problem:
        raise linear_ops.LinearError(f"{proof_card}: the {RECEIPT_MARK} receipt for "
                                     f"{fix['identifier']} could not be posted: {problem}")
    return fix


def close_lines(gaps, fix: dict | None) -> str:
    """What the PROOF close says of its gap rows: each row's criterion, kind
    and what the gap is, and the fix card that carries them."""
    rows = _rows(gaps)
    filed_as = (f", filed as {fix['identifier']} in {LANE}" if fix else "")
    return _safe_lines([
        f"It closed with {len(rows)} gap row(s) the proof cannot observe as worded"
        f"{filed_as}:",
        *(f"- “{proof_record._cut(c, _CUT)}” — {k}: {meaning(k)}" for c, k, _ in rows),
    ])


def epic_of(identifier: str) -> str | None:
    """The card's parent, or None — when it has none, or Linear could not say."""
    try:
        data = linear_ops.gql(
            "query($id: String!) { issue(id: $id) { parent { identifier } } }",
            {"id": identifier})
    except linear_ops.LinearError as exc:
        print(f"proof-gap: {identifier}'s epic could not be read: {exc}", file=sys.stderr)
        return None
    return (((data or {}).get("issue") or {}).get("parent") or {}).get("identifier")


def repo_slug_of(labels, pr_url: str) -> str:
    """The PROOF card's `repo:<slug>` label, or the record pull request's
    repository name when it carries none."""
    for name in labels or ():
        if str(name).lower().startswith("repo:"):
            return str(name).split(":", 1)[1].strip()
    found = _PR_REPO.search(pr_url or "")
    if found is None:
        raise ValueError(f"no repo label and no repository in {pr_url!r}")
    return found.group(1).lower()
