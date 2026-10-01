#!/usr/bin/env python3
"""The runner around the groom verify agent — one read-only agent per proposed
card, a fixed-shape verdict, and the step that cancels a card whose problem is
gone, or not worth solving, with its proof (DRE-4970, DRE-5304).

`groom_verify.py` (DRE-4966) reads each proposed card against its comments,
merged pull requests and other cards. It cannot read code, and code is where
DRE-2382's answer was: its file still existed, and only a reader of
`legacy_migration_lib.ts:886` and the portal roster could see the card no
longer applied. So each card the proposal would put in front of the CEO — the
Planning list and the spares behind it — gets one agent, holding
`briefs/groom-verify.md`, the card, and its repo checked out read-only under
`target/`. The agent asks whether the card's problem can still be seen there,
and answers `still-needed`, `partly-solved`, `done-elsewhere`, `obsolete` or
`not-worth-it`, each with a `file:line` proof. This file never calls a model: the vendor action in the workflow does,
and the four subcommands here are everything around it.

  * `targets` — in the groom job, which holds the Linear key: every card to
    verify, its title and body read through `linear_ops` and passed through
    `sanitize_untrusted`, its repo mapped through `config/repo-map.json`; and
    the matrix the workflow fans out over.
  * `prepare` — in the verify job, with no Linear key: the agent's whole
    input, and the moment it started.
  * `verdict` — always, even when the agent step died or never ran: the raw
    answer, checked, in the fixed shape the proposal reads.
  * `apply` — back in the groom job: a Planning card proved `done-elsewhere`,
    `obsolete` or `not-worth-it` goes on the Cancel list with the proof as its reason, the next spare
    still needed takes its slot, and the proposal id is recomputed.

**A run that dies lands as `unverified`, never as `still-needed`.** Anything
but a well-formed answer with the proof the brief requires, from a step that
succeeded, is `unverified` with the reason named; so is a target whose verdict
artifact never arrived.

**No code read, no verdict.** A card whose repo is not a key of
`config/repo-map.json` has no repository to check out, so no agent ever reads
code for it. Every subcommand holds that on its own rather than trusting the
workflow to skip the card: `targets` records it, `prepare` refuses to write an
agent input for it, `verdict` forces `unverified` whatever the raw answer
says, and `apply` does the same with or without a verdict file.

**Why the cancel here is local.** DRE-4966's cancel-and-promote lives inside
`groomer.propose(verified=…)`, which rebuilds the whole proposal from the
lane's cards, the cycles and the shaping flags. `apply` holds only the
proposal record, so it cannot call that without reading the lane again — and a
second read of the lane is a second proposal, not this one checked. What is
here is the smallest walk over the record that does the same thing: the same
slots, the same next-card-in-order rule, and `groomer.proposal_id` and
`groomer.assert_disjoint` on the result.

Every input comes through a flag; nothing is read from the environment.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import execution_result  # noqa: E402 — the one loader of the execution file
import groom_verify  # noqa: E402 — the spare count and source names
import groomer  # noqa: E402 — proposal_id, assert_disjoint
import linear_ops  # noqa: E402 — the Linear read `targets` makes
import planning_classify  # noqa: E402 — which model answered
import sanitize_untrusted  # noqa: E402 — the fence, made mechanical

ROOT = Path(__file__).resolve().parent.parent
REPO_MAP = ROOT / "config" / "repo-map.json"
BRIEF = ROOT / "briefs" / "groom-verify.md"

#: The verdicts, exact strings — the proposal, the workflow, the page and
#: every sibling of DRE-5304 read these. The model gives one of the first five
#: or `unverified`; `excluded` is the runner's word, never the model's.
(STILL_NEEDED, PARTLY_SOLVED, DONE_ELSEWHERE, OBSOLETE, NOT_WORTH_IT,
 UNVERIFIED, EXCLUDED) = ("still-needed", "partly-solved", "done-elsewhere",
                          "obsolete", "not-worth-it", "unverified", "excluded")
VERDICTS = (STILL_NEEDED, PARTLY_SOLVED, DONE_ELSEWHERE, OBSOLETE,
            NOT_WORTH_IT, UNVERIFIED, EXCLUDED)
#: The answers that put a card on the Cancel list.
CANCELS = (DONE_ELSEWHERE, OBSOLETE, NOT_WORTH_IT)
#: The answers that need no `file:line` proof — every other one does.
_UNPROVED = (UNVERIFIED, EXCLUDED)

#: Why a verdict is `unverified`, exact strings.
STEP_FAILED = "agent step failed"
STEP_SKIPPED = "agent step skipped"
NO_FILE = "no verdict file"
NO_PROOF = "no proof"
UNREADABLE = "unreadable answer"
NO_ARTIFACT = "no verdict artifact"
UNMAPPED = "repo not in config/repo-map.json: {slug}"
#: `prepare`'s whole output for a card no agent may read code for.
NOT_VERIFIABLE = "not verifiable: repo not in config/repo-map.json"

#: The `source` of a Cancel row this writes.
CANCEL_SOURCE = "verify-agent"
#: The file the agent writes, at the workspace root.
RAW_FILE = "verify-verdict.json"
#: The file each verify job uploads, as artifact `groom-verdict-<card>`.
VERDICT_FILE = "verdict.json"

STEP_OUTCOMES = ("success", "failure", "skipped", "cancelled")

FENCE_BEGIN = "===== BEGIN UNTRUSTED CARD TEXT ====="
FENCE_END = "===== END UNTRUSTED CARD TEXT ====="

CARD_QUERY = """query($id: String!) {
  issue(id: $id) { identifier title description }
}"""

#: The fields of a Planning row, copied off the sequence row a spare carries.
_NOW_FIELDS = ("identifier", "title", "cycle", "cycle_id", "unit", "epic",
               "repo", "projected", "band")


class VerifyError(Exception):
    """An input the runner cannot act on — said, and a non-zero exit."""


# --------------------------------------------------------------------------- #
# small reads                                                                  #
# --------------------------------------------------------------------------- #

def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _moment(iso) -> datetime | None:
    if not isinstance(iso, str) or not iso.strip():
        return None
    try:
        moment = datetime.fromisoformat(iso.strip().replace("Z", "+00:00"))
    except ValueError:
        return None
    return moment if moment.tzinfo else moment.replace(tzinfo=timezone.utc)


def _load(path) -> object:
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)


def _dump(path, doc) -> None:
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(doc, fh, indent=2)
        fh.write("\n")


def _one_line(text) -> str:
    return " ".join(str(text or "").split())


def _fenced(text: str) -> str:
    """Defang any sentinel-shaped line not already defanged — the targets
    file was sanitized when it was written, and this holds for one that was
    not without prefixing a caught line twice."""
    out = []
    for line in (text or "").replace("\r\n", "\n").replace("\r", "\n").split("\n"):
        if (sanitize_untrusted.SENTINEL_RE.search(line)
                and not line.startswith(sanitize_untrusted.DEFANG_PREFIX)):
            line = sanitize_untrusted.DEFANG_PREFIX + line
        out.append(line)
    return "\n".join(out)


def unmapped_reason(row: dict) -> str:
    return UNMAPPED.format(slug=row.get("repo_slug") or "none")


def _is_unmapped(row: dict) -> bool:
    """A target row that says no repository was mapped. A row that says
    nothing either way — `apply` over a record with no targets — is not."""
    return "repository" in row and not row.get("repository")


def _target(rows: list[dict], card: str) -> dict:
    for row in rows:
        if isinstance(row, dict) and row.get("card") == card:
            return row
    raise VerifyError(f"{card} is not in the targets file")


def _targets_file(path) -> list[dict]:
    try:
        rows = _load(path)
    except (OSError, ValueError) as e:
        raise VerifyError(f"the targets file {path} could not be read: {e}")
    if not isinstance(rows, list):
        raise VerifyError(f"the targets file {path} is not a list")
    return rows


# --------------------------------------------------------------------------- #
# targets                                                                      #
# --------------------------------------------------------------------------- #

def _planning(proposal: dict) -> list[str]:
    return [r["identifier"] for r in
            sorted(proposal["outcomes"]["now"], key=lambda r: r["position"])]


def spares(proposal: dict, spare: int = groom_verify.SPARE) -> list[str]:
    """The spare cards behind the Planning list, in order.

    DRE-4966's record marks them: `verification.cards` rows whose `list` is
    `spare`. A marked spare that has since taken a Planning slot or gone on
    the Cancel list is not a spare any more. A record the check never ran on
    carries no marking, and then the spares are the first `spare` `not-now`
    cards in sequence order that are scheduled into a cycle — a card with no
    cycle is one the read declined, and has no slot to give.
    """
    waiting = {r["identifier"] for r in proposal["outcomes"]["not-now"]
               if r.get("reconsidered_in") is not None}
    cancel = {r["identifier"] for r in proposal["outcomes"]["dead"]}
    block = proposal.get("verification")
    if isinstance(block, dict) and isinstance(block.get("cards"), list):
        marked = [r.get("identifier") for r in block["cards"]
                  if isinstance(r, dict) and r.get("list") == "spare"]
        return [i for i in marked if i in waiting and i not in cancel]
    ordered = [r["identifier"] for r in proposal["sequence"]
               if r["identifier"] in waiting and r["identifier"] not in cancel]
    return ordered[:spare]


def window(proposal: dict) -> list[tuple[str, str]]:
    """`(card, "planning" | "spare")`, Planning in position order first."""
    return ([(i, "planning") for i in _planning(proposal)]
            + [(i, "spare") for i in spares(proposal)])


def _layer_a(proposal: dict, identifier: str) -> list[str]:
    """What DRE-4966's check recorded for the card, one line each — quoted
    comments among it, so it is sanitized like the card's own text."""
    rows = ((proposal.get("verification") or {}).get("cards") or [])
    out: list[str] = []
    for row in rows:
        if not isinstance(row, dict) or row.get("identifier") != identifier:
            continue
        for item in row.get("evidence") or []:
            text = item.get("text") if isinstance(item, dict) else item
            if text:
                out.append(sanitize_untrusted.sanitize_line(str(text)))
        unread = [groom_verify.SOURCE_NAMES.get(s, s)
                  for s in row.get("unread") or []]
        if unread:
            out.append("not read this run: " + ", ".join(unread))
    return out


def _repo(row: dict, repo_map: dict) -> tuple[str | None, str | None]:
    """`(owner/repo or None, the row's repo slug or None)`."""
    slug = row.get("repo")
    slug = None if slug in (None, "", groomer.NO_REPO) else slug
    return (repo_map.get(slug) if slug else None), slug


def targets(proposal: dict, *, lops, repo_map: dict) -> list[dict]:
    rows = {r["identifier"]: r for r in proposal["sequence"]}
    out = []
    for identifier, which in window(proposal):
        row = rows.get(identifier) or {}
        repository, slug = _repo(row, repo_map)
        evidence = _layer_a(proposal, identifier)
        try:
            issue = (lops.gql(CARD_QUERY, {"id": identifier}) or {}).get("issue") or {}
        except Exception as e:  # noqa: BLE001 — an unread card is said, not fatal
            issue = {}
            evidence.append(f"the card's text could not be read from Linear "
                            f"this run: {_one_line(e)}")
        out.append({
            "card": identifier,
            "repository": repository,
            "repo_slug": slug,
            "title": sanitize_untrusted.sanitize_line(
                issue.get("title") or row.get("title") or ""),
            "body": sanitize_untrusted.sanitize_body(issue.get("description") or ""),
            "evidence": evidence,
            "list": which,
        })
    return out


def matrix(rows: list[dict]) -> list[dict]:
    """The workflow's matrix: the empty string, not null, marks an unmapped
    repo, so `matrix.repository != ''` reads it."""
    return [{"card": r["card"], "repository": r.get("repository") or ""}
            for r in rows]


# --------------------------------------------------------------------------- #
# prepare                                                                      #
# --------------------------------------------------------------------------- #

SHAPE = f"""Write exactly one file, `{RAW_FILE}`, at the workspace root:

```json
{{
  "card": "{{card}}",
  "verdict": "still-needed | partly-solved | done-elsewhere | obsolete | not-worth-it | unverified",
  "summary": "One or two sentences.",
  "proof": [
    {{"file": "<path under target/>", "line": 1, "quote": "<the line>"}},
    {{"source": "<where>", "quote": "<the words>"}}
  ]
}}
```

Every answer but `unverified` needs at least one `file:line` proof from
`target/`. If `target/` is absent or empty, the only answer is `unverified`."""


def agent_input(row: dict, brief: str) -> str:
    card = row["card"]
    evidence = row.get("evidence") or []
    lines = [brief.rstrip("\n"), "", "## The card", "", FENCE_BEGIN,
             f"Card: {card}",
             f"Title: {_fenced(sanitize_untrusted.sanitize_line(row.get('title') or ''))}",
             "", _fenced(row.get("body") or ""), FENCE_END, "",
             "## The Layer A evidence", "", FENCE_BEGIN]
    lines += ([f"- {_fenced(_one_line(e))}" for e in evidence]
              or ["None recorded."])
    lines += [FENCE_END, "", "## The verdict file", "",
              SHAPE.replace("{card}", card), ""]
    return "\n".join(lines)


def prepare(rows: list[dict], card: str, *, brief: str) -> str:
    row = _target(rows, card)
    if _is_unmapped(row):
        return NOT_VERIFIABLE + "\n"
    return agent_input(row, brief)


# --------------------------------------------------------------------------- #
# verdict                                                                      #
# --------------------------------------------------------------------------- #

class _Unreadable(Exception):
    pass


def _proof_item(item) -> dict:
    """One proof item in its exact shape, or `_Unreadable`."""
    if not isinstance(item, dict):
        raise _Unreadable
    if "file" in item:
        path, line, quote = item.get("file"), item.get("line"), item.get("quote")
        if (not isinstance(path, str) or not path.strip()
                or isinstance(line, bool) or not isinstance(line, int)
                or line < 1 or not isinstance(quote, str)):
            raise _Unreadable
        return {"file": _one_line(path), "line": line,
                "quote": sanitize_untrusted.sanitize_line(quote)}
    source, quote = item.get("source"), item.get("quote")
    if not isinstance(source, str) or not isinstance(quote, str):
        raise _Unreadable
    return {"source": sanitize_untrusted.sanitize_line(source),
            "quote": sanitize_untrusted.sanitize_line(quote)}


def is_file_line(item: dict) -> bool:
    """A `file:line` item naming a line inside `target/` and quoting it."""
    path = str(item.get("file") or "")
    parts = Path(path).parts
    return ("file" in item and bool(path) and not Path(path).is_absolute()
            and ".." not in parts and bool(_one_line(item.get("quote"))))


def _counts(item: dict, card_text: str) -> bool:
    """Is this item proof? Quoted card text is never proof on its own."""
    if is_file_line(item):
        return True
    quote = _one_line(item.get("quote"))
    return "source" in item and bool(quote) and quote not in card_text


def judge(raw_path, *, card: str, row: dict, outcome: str) -> dict:
    """`{verdict, summary, proof, reason}` — the answer as the proposal may
    read it. The target row first, then the step, then the file."""
    def unverified(reason: str, summary: str) -> dict:
        return {"verdict": UNVERIFIED, "summary": summary, "proof": [],
                "reason": reason}

    if _is_unmapped(row):
        return unverified(unmapped_reason(row),
                          "No code was read: the card's repo is not one the "
                          "pipeline can check out.")
    if outcome != "success":
        reason = STEP_SKIPPED if outcome == "skipped" else STEP_FAILED
        return unverified(reason, f"The agent step did not succeed "
                                  f"({outcome or 'no outcome'}), so nothing "
                                  f"was verified.")
    if not raw_path or not os.path.isfile(raw_path):
        return unverified(NO_FILE, f"The agent wrote no {RAW_FILE}.")
    try:
        doc = _load(raw_path)
        if (not isinstance(doc, dict) or doc.get("card") != card
                or doc.get("verdict") not in VERDICTS
                # The model may not exclude a card: only `targets` may.
                or doc.get("verdict") == EXCLUDED
                or not isinstance(doc.get("summary"), str)
                or not doc["summary"].strip()
                or not isinstance(doc.get("proof"), list)):
            raise _Unreadable
        proof = [_proof_item(item) for item in doc["proof"]]
    except (OSError, ValueError, _Unreadable):
        return unverified(UNREADABLE, f"The agent's {RAW_FILE} could not be "
                                      f"read as an answer.")
    verdict = doc["verdict"]
    summary = sanitize_untrusted.sanitize_line(doc["summary"])
    card_text = _one_line(f"{row.get('title') or ''} {row.get('body') or ''}")
    if verdict == UNVERIFIED:
        return unverified(NO_PROOF, summary)
    if not any(is_file_line(p) for p in proof):
        return unverified(NO_PROOF, summary)
    if not any(_counts(p, card_text) for p in proof):
        return unverified(NO_PROOF, summary)
    return {"verdict": verdict, "summary": summary, "proof": proof,
            "reason": None}


def spend(execution_file) -> dict:
    record = execution_result.load_execution(execution_file) if execution_file else None
    scalars = execution_result.spend_scalars(record)
    cost = scalars.get("total_cost_usd")
    duration = scalars.get("duration_ms")
    return {"cost_usd": float(cost) if cost is not None else None,
            "duration_ms": int(duration) if duration is not None else None,
            "model": planning_classify.answered_model(record)}


def verdict(*, card: str, rows: list[dict], raw, execution_file,
            outcome: str, started_at) -> dict:
    row = _target(rows, card)
    answer = judge(raw, card=card, row=row, outcome=outcome)
    return {"card": card, **answer, **spend(execution_file),
            "started_at": started_at, "finished_at": _now()}


def _started(path) -> str | None:
    try:
        with open(path, encoding="utf-8") as fh:
            text = fh.read().strip()
    except OSError:
        return None
    return text if _moment(text) else None


# --------------------------------------------------------------------------- #
# apply                                                                        #
# --------------------------------------------------------------------------- #

def read_verdicts(directory) -> dict[str, dict]:
    """Every `verdict.json` under the directory — one per downloaded
    artifact — by card. A file that cannot be read is not a verdict."""
    found: dict[str, dict] = {}
    for path in sorted(Path(directory).rglob(VERDICT_FILE)):
        try:
            doc = _load(path)
        except (OSError, ValueError):
            continue
        if isinstance(doc, dict) and isinstance(doc.get("card"), str):
            found.setdefault(doc["card"], doc)
    return found


def _mark(row: dict, doc: dict | None) -> dict:
    """The `verify` field a verified row carries, plus the two times the
    wall clock is read from."""
    blank = {"summary": None, "proof": [], "cost_usd": None,
             "duration_ms": None, "model": None, "started_at": None,
             "finished_at": None}
    if _is_unmapped(row):
        return {**blank, **_spend_of(doc), "verdict": UNVERIFIED,
                "reason": unmapped_reason(row)}
    if doc is None:
        return {**blank, "verdict": UNVERIFIED, "reason": NO_ARTIFACT}
    mark = {**blank, **_spend_of(doc),
            "verdict": doc.get("verdict"),
            "summary": doc.get("summary"),
            "proof": [p for p in (doc.get("proof") if isinstance(
                doc.get("proof"), list) else []) if isinstance(p, dict)],
            "reason": doc.get("reason")}
    if mark["verdict"] not in VERDICTS or mark["verdict"] == EXCLUDED:
        mark.update(verdict=UNVERIFIED, reason=UNREADABLE, proof=[])
    elif mark["verdict"] not in _UNPROVED and not any(
            is_file_line(p) for p in mark["proof"]):
        # `verdict` never writes this; a file that says it anyway is not
        # allowed to stand, or cancel a card, on no proof.
        mark.update(verdict=UNVERIFIED, reason=NO_PROOF, proof=[])
    elif mark["verdict"] == UNVERIFIED and not mark["reason"]:
        mark["reason"] = NO_PROOF
    elif mark["verdict"] != UNVERIFIED:
        mark["reason"] = None
    return mark


def _spend_of(doc: dict | None) -> dict:
    doc = doc or {}
    cost, duration, model = (doc.get("cost_usd"), doc.get("duration_ms"),
                             doc.get("model"))
    return {
        "cost_usd": (cost if isinstance(cost, (int, float))
                     and not isinstance(cost, bool) else None),
        "duration_ms": (duration if isinstance(duration, int)
                        and not isinstance(duration, bool) else None),
        "model": model if isinstance(model, str) else None,
        "started_at": doc.get("started_at"),
        "finished_at": doc.get("finished_at"),
    }


def cancel_reason(mark: dict) -> str:
    """`<verdict>: <summary>`, then each proof as `file:line — quote` or
    `source — quote` — the line the CEO reads and the drain writes onto the
    card. One line, defanged like every other reason on the page."""
    parts = [f"{mark['verdict']}: {_one_line(mark.get('summary'))}"]
    for item in mark.get("proof") or []:
        if "file" in item:
            parts.append(f"{_one_line(item.get('file'))}:{item.get('line')} — "
                         f"{_one_line(item.get('quote'))}")
        else:
            parts.append(f"{_one_line(item.get('source'))} — "
                         f"{_one_line(item.get('quote'))}")
    return groomer.defang_reason("; ".join(parts))[0]


def _verify_field(mark: dict) -> dict:
    return {k: mark.get(k) for k in ("verdict", "summary", "proof", "reason",
                                     "cost_usd", "duration_ms", "model")}


def _target_rows(proposal: dict, rows: list[dict] | None,
                 repo_map: dict) -> list[dict]:
    """`--targets`, else the record's `verify_targets`, else the window
    `targets` would have listed, mapped through the same repo map — so an
    unmapped card is known to be one even when no targets file came back."""
    if rows is not None:
        return rows
    recorded = proposal.get("verify_targets")
    if isinstance(recorded, list):
        return recorded
    by_seq = {r["identifier"]: r for r in proposal["sequence"]}
    out = []
    for identifier, which in window(proposal):
        repository, slug = _repo(by_seq.get(identifier) or {}, repo_map)
        out.append({"card": identifier, "repository": repository,
                    "repo_slug": slug, "list": which})
    return out


def apply(proposal: dict, found: dict[str, dict],
          target_rows: list[dict] | None = None, *,
          repo_map: dict | None = None) -> dict:
    if repo_map is None:
        repo_map = _load(REPO_MAP)
    rows = [r for r in _target_rows(proposal, target_rows, repo_map)
            if isinstance(r, dict) and isinstance(r.get("card"), str)]
    marks = {r["card"]: _mark(r, found.get(r["card"])) for r in rows}
    outcomes, seq = proposal["outcomes"], proposal["sequence"]
    by_seq = {r["identifier"]: r for r in seq}
    now = sorted(outcomes["now"], key=lambda r: r["position"])
    waiting = {r["identifier"]: r for r in outcomes["not-now"]}

    # The spares, in target order: cards the proposal left waiting in a
    # cycle. A card with no cycle was declined by the read and has no slot to
    # take, whatever its answer.
    spare_ids = [r["card"] for r in rows if r["card"] in waiting
                 and waiting[r["card"]].get("reconsidered_in") is not None]
    queue = iter(i for i in spare_ids if marks[i]["verdict"] == STILL_NEEDED)

    canceled: list[str] = []
    promoted: dict[str, dict] = {}
    kept, unfilled = [], 0
    for slot in now:
        identifier = slot["identifier"]
        if marks.get(identifier, {}).get("verdict") not in CANCELS:
            kept.append(slot)
            continue
        canceled.append(identifier)
        taker = next(queue, None)
        if taker is None:
            unfilled += 1
            continue
        promoted[taker] = slot
        base = by_seq.get(taker) or waiting[taker]
        row = {k: base.get(k) for k in _NOW_FIELDS}
        row.update(identifier=taker, position=slot["position"],
                   cycle=slot.get("cycle"), cycle_id=slot.get("cycle_id"))
        if base.get("held"):
            row["held"] = True
        row.update(reason=groomer._rules_reason("now", row), trigger=None,
                   evidence=None, judged=False, reasons={})
        kept.append(row)
    # A spare proved done elsewhere, obsolete or not worth it is canceled
    # the same way, in order.
    canceled += [i for i in spare_ids
                 if marks[i]["verdict"] in CANCELS and i not in promoted]

    kept.sort(key=lambda r: r["position"])
    if unfilled:
        for n, row in enumerate(kept, 1):
            row["position"] = n
    outcomes["now"] = kept
    gone = set(canceled) | set(promoted)
    outcomes["not-now"] = [r for r in outcomes["not-now"]
                           if r["identifier"] not in gone]
    for identifier in canceled:
        base = by_seq.get(identifier) or {}
        reason = cancel_reason(marks[identifier])
        outcomes["dead"].append({
            "identifier": identifier, "title": base.get("title"),
            "repo": base.get("repo"), "superseded_by": None,
            "source": CANCEL_SOURCE, "position": len(outcomes["dead"]) + 1,
            "epic": base.get("epic"), "band": base.get("band"),
            "reason": reason, "trigger": None, "evidence": reason,
            "judged": False, "reasons": {}})

    by_now = {r["identifier"]: r for r in kept}
    by_dead = {r["identifier"]: r for r in outcomes["dead"]}
    for row in seq:
        identifier = row["identifier"]
        if identifier in by_dead and identifier in canceled:
            dead = by_dead[identifier]
            row.update(outcome="dead", cycle=None, cycle_id=None,
                       projected=False, source=CANCEL_SOURCE,
                       reason=dead["reason"], trigger=None,
                       evidence=dead["evidence"], judged=False, reasons={})
        elif identifier in promoted:
            now_row = by_now[identifier]
            row.update(outcome="now", cycle=now_row["cycle"],
                       cycle_id=now_row["cycle_id"], reason=now_row["reason"],
                       trigger=None)

    for rows_ in (outcomes["now"], outcomes["not-now"], outcomes["dead"], seq):
        for row in rows_:
            if row["identifier"] in marks:
                row["verify"] = _verify_field(marks[row["identifier"]])

    proposal["batch"]["cards"] = len(kept)
    proposal["deprioritised"] = groomer._deprioritised(proposal)
    proposal["verify"] = summary(marks, [r["card"] for r in rows], unfilled)
    groomer.assert_disjoint(proposal)
    proposal["id"] = groomer.proposal_id(proposal)
    return proposal


def summary(marks: dict[str, dict], order: list[str], unfilled: int) -> dict:
    counts = {v: 0 for v in VERDICTS}
    for identifier in order:
        counts[marks[identifier]["verdict"]] += 1
    costs = [m["cost_usd"] for m in marks.values() if m["cost_usd"] is not None]
    starts = [t for t in (_moment(m["started_at"]) for m in marks.values()) if t]
    ends = [t for t in (_moment(m["finished_at"]) for m in marks.values()) if t]
    # The span from the first agent's start to the last one's finish — the
    # agents run side by side, so the durations' sum is not how long it took.
    wall = (round((max(ends) - min(starts)).total_seconds())
            if starts and ends else None)
    return {
        "cards": len(order),
        "counts": counts,
        "cost_usd": round(sum(costs), 6) if costs else None,
        "wall_clock_seconds": wall,
        "unverified": [i for i in order if marks[i]["verdict"] == UNVERIFIED],
        # `{"identifier", "reason"}` per card `targets` excluded without
        # judgement — none yet: DRE-5306 writes the first.
        "excluded": [],
        "slots_unfilled": unfilled,
    }


# --------------------------------------------------------------------------- #
# the command line                                                             #
# --------------------------------------------------------------------------- #

def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("targets", help="the cards to verify, and the matrix")
    p.add_argument("--proposal", required=True)
    p.add_argument("--out", required=True)
    p.add_argument("--matrix-out", dest="matrix_out", required=True)
    p.add_argument("--repo-map", dest="repo_map", default=str(REPO_MAP))

    p = sub.add_parser("prepare", help="one card's agent input")
    p.add_argument("--targets", required=True)
    p.add_argument("--card", required=True)
    p.add_argument("--out", required=True)
    p.add_argument("--started-at-out", dest="started_at_out", required=True)
    p.add_argument("--brief", default=str(BRIEF))

    p = sub.add_parser("verdict", help="the agent's answer, in the fixed shape")
    p.add_argument("--card", required=True)
    p.add_argument("--targets", required=True)
    p.add_argument("--raw", required=True)
    p.add_argument("--execution-file", dest="execution_file", default="")
    p.add_argument("--step-outcome", dest="step_outcome", required=True)
    p.add_argument("--started-at", dest="started_at", required=True)
    p.add_argument("--out", required=True)

    p = sub.add_parser("apply", help="write the verdicts onto the proposal")
    p.add_argument("--proposal", required=True)
    p.add_argument("--verdicts", required=True)
    p.add_argument("--out", required=True)
    p.add_argument("--targets")
    p.add_argument("--repo-map", dest="repo_map", default=str(REPO_MAP))
    return parser


def main(argv=None, *, lops=None) -> int:
    args = _parser().parse_args(argv)
    try:
        return _run(args, lops=lops or linear_ops)
    except VerifyError as e:
        print(f"groom-verify: {e}", file=sys.stderr)
        return 2


def _run(args, *, lops) -> int:
    if args.command == "targets":
        proposal = _load(args.proposal)
        rows = targets(proposal, lops=lops, repo_map=_load(args.repo_map))
        _dump(args.out, rows)
        _dump(args.matrix_out, matrix(rows))
        unmapped = [r["card"] for r in rows if _is_unmapped(r)]
        print(f"groom-verify: {len(rows)} card(s) to verify"
              + (f"; no repo to read for {', '.join(unmapped)}"
                 if unmapped else ""))
        return 0

    if args.command == "prepare":
        started = _now()
        with open(args.started_at_out, "w", encoding="utf-8") as fh:
            fh.write(started + "\n")
        brief = Path(args.brief).read_text(encoding="utf-8")
        text = prepare(_targets_file(args.targets), args.card, brief=brief)
        with open(args.out, "w", encoding="utf-8") as fh:
            fh.write(text)
        if text.strip() == NOT_VERIFIABLE:
            print(NOT_VERIFIABLE)
        else:
            print(f"groom-verify: wrote the agent input for {args.card}")
        return 0

    if args.command == "verdict":
        doc = verdict(card=args.card, rows=_targets_file(args.targets),
                      raw=args.raw, execution_file=args.execution_file,
                      outcome=args.step_outcome,
                      started_at=_started(args.started_at))
        _dump(args.out, doc)
        print(f"groom-verify: {args.card} — {doc['verdict']}"
              + (f" ({doc['reason']})" if doc["reason"] else ""))
        return 0

    proposal = _load(args.proposal)
    rows = _targets_file(args.targets) if args.targets else None
    before = proposal.get("id")
    after = apply(proposal, read_verdicts(args.verdicts), rows,
                  repo_map=_load(args.repo_map))
    _dump(args.out, after)
    block = after["verify"]
    print(f"groom-verify: {block['cards']} card(s) — "
          + ", ".join(f"{n} {v}" for v, n in block["counts"].items())
          + f"; {block['slots_unfilled']} slot(s) unfilled; "
            f"id {before} → {after['id']}")
    return 0


if __name__ == "__main__":                                  # pragma: no cover
    sys.exit(main())
