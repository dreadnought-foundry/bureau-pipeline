#!/usr/bin/env python3
"""A PROOF card open more than seven days, read into one of five waits (DRE-8625).

Since 2026-10-01, 59 of the 72 cards that waited more than 24 hours in
`In Review` were proof records, and on 2026-11-01 DRE-6673, DRE-6008 and
DRE-5357 still stood. Each carried `🔬 proof-waiting` holds and
`🧱 proof-record-stuck` notes, and nothing said, in one word from a fixed
list, what the proof was waiting on — so nothing could take the one action
that would move it. This module is that word's one reader. It is pure and
read-only: it writes no comment, no hold, no lane and no label, and nothing
calls it yet. The daily pass (`scripts/proof_overdue_sweep.py`) and the
action step (`scripts/proof_overdue_act.py`) read its answer, so the names
below are a contract with them. `docs/proof-overdue.md` states the rules in
plain words.

## The age

A proof is overdue when its age is more than `AGE_DAYS`. The clock starts
at the EARLIEST of the card's first `🔬 proof-run` receipt
(`proof_run_state.receipt`) and its OPEN record pull request's `createdAt`;
a card with neither is clocked from its entry into the lane it is in now,
the newest move into that lane on the card's history (its creation when the
history names none). `age_days` is a float, printed to one decimal.

## The five classes

`CLASSES`, in its order, which is also the precedence when a card's holds
and rows read more than one — the first class in it that the card reads is
the one it gets:

  * `release` — a release not yet out (`proof_release.reading`, read only
    for a card never run), or a named card whose shipping a row or a hold
    waits on and which is not `Done`: a `<DRE-n> shipped` needs half
    (`proof_dispatch.FIX_SHIPPED_NEEDS`), a card hold whose needs half
    opens with card ids (`proof_dispatch._card_hold`), a waiting row's
    event naming `DRE-<n> shipped` (or ships, shipping, merged, live), or a
    row's `fix: DRE-<n>` (`proof_record.row_fixes`) — or a card on one of
    its `blocks` relations that is not closed (`prose_blockers.TERMINAL`),
    the dispatcher's condition 3, which holds a first run and names no hold.
    `waits_on` is the id, or the release's tag.
  * `date` — a waiting row stamped `[not-before=<T>]`
    (`proof_record.row_not_before`), or a time-only hold
    (`proof_record.time_only`) with no waiting row behind it. `waits_on` is
    the latest such time, in UTC ISO 8601, and `not_before` the same time.
  * `press` — a press on the sandbox list (`plan_proof_reach.sandbox_presses`,
    the copy of `standards/card-quality.md`'s one list), on the sandbox, on a
    PROOF labeled `repo:agent-bureau` (`SANDBOX_REPO`). `waits_on` is the
    mutation, such as `createTask`.
  * `deploy` — a row's event or a hold's needs half naming a deploy: a
    deployment record, a deploy run, a service rollout. `waits_on` is the
    deploy named — its backticked command, or the clause that names it.
  * `unrepeatable` — an observation that cannot recur unattended: a history
    row (`proof_record.row_history`), a `state-gone` cause row, a hold that
    names a closed window or a finished run, or any press that is not
    `press` above — off the list, off the sandbox, on a PROOF for another
    repo, or the CEO's own (`proof_record.CEO_PRESS`). It is also read BY
    ELIMINATION (`by_elimination`) when an overdue card reads none of the
    other four: something holds it, and it is nothing the pipeline will
    wait out. `waits_on` is the criterion cut to 70 characters.

Each class takes one action, `ACTIONS`, and the action step is the one that
takes it.

## The read-through

A time-only hold the dispatcher posts to re-check its record's waiting rows
or to time a re-run (DRE-7951, `proof_dispatch.RECHECK_REASON`,
`TIMED_REASON`) is the pipeline's own timer, not what the proof waits on:
the rows it re-checks are. So a time-only hold is read as `date` only when
the record has no waiting row behind it; otherwise it is read past, and the
rows decide. A waiting row whose own event is a date still reads `date`.

## What is read

The card's open `🔬 proof-waiting` holds (`proof_record.open_holds`, over
`spoken_thread.voices`), the unmet rows of its record — read from its open
record pull request at the head, as `proof_dispatch.record_pr` and
`record_at` read it, or from its record branch at its head when none is open
— and, for a card never run, the release reading. For diagnosis only, a
record pull request that MODIFIES its one record file under `docs/` or
`architecture/` instead of adding one (DRE-5357's #3876) is still read
(`record_path`); the gate's refusal of that shape is unchanged. The states of
the cards the holds and rows name are read in one request, and the card's
`blocks` relations ride on the card's own read.

`diagnose` is the judgment, over facts already read. `reading` reads them —
`gather` assembles the saved card — and `from_saved` judges a saved card, so
a card copied off the board replays after the board has moved.

## The replay

    proof_overdue.py replay --repo <slug>
    proof_overdue.py replay --fixture <path>

reads the repo's open `PROOF:` cards in every open lane, read-only — or one
saved card — and prints one line per overdue card:

    proof-overdue: DRE-<n> · <age> days · class=<class> · action=<action> · waits-on=<waits_on>

and `proof-overdue: none overdue in <slug>` when there are none. Exit 0 on a
completed read, 1 when a card could not be read, named on its own line and
never skipped silently.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import card_pr  # noqa: E402 — the record pull request, found as the dispatcher finds it
import linear_ops  # noqa: E402
import plan_footprint  # noqa: E402 — a branch record named on the card's **Files:** line
import plan_proof_reach  # noqa: E402 — the sandbox presses, the one copy of the list
import proof_and_demo  # noqa: E402 — what a PROOF title is
import proof_record  # noqa: E402 — the holds and the record's rows, read as the gate reads them
import proof_release  # noqa: E402
import proof_run_state  # noqa: E402 — the `🔬 proof-run` receipt
import prose_blockers  # noqa: E402 — the closed states a sibling's merge is read in
import spoken_thread  # noqa: E402 — who said each comment

#: Opens every line this module prints.
TAG = "proof-overdue:"
#: A proof is overdue when its age, in days, is more than this.
AGE_DAYS = 7
#: The five waits, in their order, which is also their precedence.
CLASSES = ("release", "date", "press", "deploy", "unrepeatable")
#: The one machine action each class takes.
ACTIONS = {"release": "rerun-on-release", "date": "rerun-on-date", "press": "sandbox-press",
           "deploy": "rerun-on-deploy", "unrepeatable": "split"}

#: How much of a criterion an `unrepeatable` reading names, cut as
#: `proof_record` cuts a row.
CUT = 70
#: The one repo whose proof run holds the console proof-reader, and so the
#: one whose PROOF a sandbox press is `press` on (DRE-7772).
SANDBOX_REPO = "agent-bureau"
#: The state a named card has shipped in.
SHIPPED = "Done"
#: What an elimination names when the card has no hold and no unmet row.
NOTHING_NAMED = "no hold and no unmet row names it"
REPO_MAP = Path(__file__).resolve().parent.parent / "config" / "repo-map.json"

#: A card named as shipping: `DRE-<n> shipped`, `DRE-<n>'s merge`, `DRE-<n> is live`.
_SHIPPING = re.compile(r"\b(DRE-\d+)(?:['’]s)?\s+(?:(?:is|has|to be|being|been)\s+)?"
                       r"(?:to\s+)?(?:ship(?:s|ped|ping)?|merg(?:e|es|ed|ing)|land(?:s|ed|ing)?"
                       r"|(?:goes\s+|going\s+)?live)\b", re.IGNORECASE)
_DEPLOY = re.compile(r"\b(?:re-?)?deploy[\w-]*|\broll(?:s|ed|ing)?[ -]out\b|\brollouts?\b",
                     re.IGNORECASE)
_BACKTICKED = re.compile(r"`([^`]+)`")
_CLAUSE = re.compile(r"[,;()]|\s+(?:and|or|then)\s+")
_LEAD = re.compile(r"^(?:for|on|until|after|once)\s+", re.IGNORECASE)
_PRESS = re.compile(r"\bpress(?:es|ed|ing)?\b", re.IGNORECASE)
_SANDBOX = re.compile("|".join([r"\bsandbox press\b"] + [
    rf"(?<![\w-]){re.escape(name)}(?![\w-])" for name in plan_proof_reach.SANDBOX_NAMES]),
    re.IGNORECASE)
#: A hold naming an observation whose window or run is over (DRE-7740).
_CLOSED = re.compile(r"\bclosed window|\bwindow (?:had |has )?closed\b|\bfinished run\b"
                     r"|\brun (?:had |has )?finished\b", re.IGNORECASE)
#: The tag a waiting release line names: `… newest <tag> does not carry …`,
#: `… <tag> does not carry …`, or the series of `no tag in <series> yet`.
_RELEASE_TAG = re.compile(r"^waiting — [^:]+: (?:newest )?(\S+) does not carry"
                          r"|^waiting — [^:]+: no tag in (.+?) yet")
_IDS = re.compile(r"DRE-\d+")


def _dispatch():
    """`proof_dispatch`, imported on first use: it imports `reconcile`,
    which reads `REPO` when it is imported (`main` sets it)."""
    import proof_dispatch  # noqa: PLC0415

    return proof_dispatch


@dataclass(frozen=True)
class Diagnosis:
    """What an overdue PROOF card waits on, and the one action it takes."""

    card: str
    repo: str
    age_days: float
    clock_from: datetime
    cls: str
    action: str
    waits_on: str
    not_before: datetime | None
    by_elimination: bool
    evidence: str

    def line(self) -> str:
        return (f"{TAG} {self.card} · {self.age_days:.1f} days · class={self.cls} · "
                f"action={self.action} · waits-on={self.waits_on}")


# --------------------------------------------------------------------------- #
# the judgment                                                                 #
# --------------------------------------------------------------------------- #


def _time(value) -> datetime | None:
    """An aware datetime, from one or from an ISO 8601 string — or None."""
    if isinstance(value, datetime):
        return value if value.tzinfo is not None else None
    try:
        moment = datetime.fromisoformat(str(value or "").replace("Z", "+00:00"))
    except ValueError:
        return None
    return moment if moment.tzinfo is not None else None


def _utc(when: datetime) -> str:
    return when.astimezone(timezone.utc).isoformat()


def _first_line(text) -> str:
    return (text or "").strip().split("\n", 1)[0].strip()


def _unshipped(text: str, card: dict) -> list:
    """The cards `text` names as shipping that are not `SHIPPED`, in order."""
    states = card.get("states") or {}
    named = dict.fromkeys(m.group(1).upper() for m in _SHIPPING.finditer(text or ""))
    return [i for i in named if states.get(i) != SHIPPED]


def _deploy_named(text: str) -> str | None:
    """The deploy `text` names — its backticked command, else the clause
    holding the word — or None when it names none."""
    if not _DEPLOY.search(text or ""):
        return None
    for span in _BACKTICKED.findall(text):
        if _DEPLOY.search(span):
            return span.strip()
    for clause in _CLAUSE.split(text):
        if _DEPLOY.search(clause):
            return _LEAD.sub("", clause.strip().replace("`", ""))
    return proof_record._cut(text, CUT)


def _press(text: str, card: dict) -> tuple | None:
    """`("press", <mutation>)` for a sandbox press on the list, on the
    sandbox, on an agent-bureau PROOF; `("unrepeatable", None)` for any other
    press; None when `text` names no press."""
    ops = plan_proof_reach.sandbox_presses(text or "")
    if ops and card.get("repo") == SANDBOX_REPO and _SANDBOX.search(text):
        return "press", ops[0]
    if ops or _PRESS.search(text or ""):
        return "unrepeatable", None
    return None


def _event(result: str) -> str:
    """What a waiting row waits for: its event, without its stamp or the
    `: <what it would show>` after it — or "" for a row that does not wait."""
    return (proof_record.row_waiting(result) or "").split(": ", 1)[0].strip()


def _unmet(rows) -> list:
    """The judged rows that are neither met nor accepted, as (criterion, result)."""
    return [(c or "", r or "") for c, r in rows or ()
            if not proof_record.is_closing_row(c or "") and not proof_record.row_met(r or "")
            and not proof_record.row_accepted(r or "")]


def _row_reads(criterion: str, result: str, card: dict) -> list:
    """`(cls, waits_on, not_before, evidence)` for each class one unmet row reads."""
    evidence, cut = result.strip(), proof_record._cut(criterion, CUT)
    if proof_record.row_history(result) is not None or proof_record.row_cause(result) == "state-gone":
        return [("unrepeatable", cut, None, evidence)]
    out = [("release", i, None, evidence) for i in proof_record.row_fixes(result)
           if (card.get("states") or {}).get(i) != SHIPPED]
    event = _event(result)
    if event:
        out += [("release", i, None, evidence) for i in _unshipped(event, card)]
        stamp = proof_record.row_not_before(result)
        if stamp is not None:
            out.append(("date", _utc(stamp), stamp.astimezone(timezone.utc), evidence))
        deploy = _deploy_named(event)
        if deploy:
            out.append(("deploy", deploy, None, evidence))
    if event or criterion.lstrip("*_ ").lower().startswith("sandbox press:"):
        press = _press(f"{criterion} {event}", card)
        if press is not None:
            out.append((press[0], press[1] or cut, None, evidence))
    return out


def _observed(line: str) -> str:
    """A hold's observed half: between its mark and its last `— needs`."""
    text = line[len(proof_record.HOLD_MARK):] if line.startswith(proof_record.HOLD_MARK) else line
    return text.rpartition(" — needs")[0].strip() or text.strip()


def _hold_reads(line: str, card: dict, *, behind: bool) -> list:
    """`(cls, waits_on, not_before, evidence)` for each class one open hold
    reads. `behind` is whether the record has a waiting row behind a
    time-only hold, which is then read through (DRE-7951)."""
    dispatch = _dispatch()
    line = _first_line(line)
    until = proof_record.time_only(line)
    if until is not None:
        return [] if behind else [("date", _utc(until), until.astimezone(timezone.utc), line)]
    needs, cut = dispatch._needs_half(line), proof_record._cut(_observed(line), CUT)
    states = card.get("states") or {}
    named = list(dispatch._card_hold(line))
    shipped = dispatch._CARDS_SHIPPED.match(needs)
    named += _IDS.findall(shipped.group(0)) if shipped else []
    named += [m.group(1).upper() for m in _SHIPPING.finditer(needs)]
    out = [("release", i, None, line) for i in dict.fromkeys(named) if states.get(i) != SHIPPED]
    deploy = _deploy_named(needs)
    if deploy:
        out.append(("deploy", deploy, None, line))
    if proof_record.CEO_PRESS in line:
        out.append(("unrepeatable", cut, None, line))
    else:
        press = _press(needs, card)
        if press is not None:
            out.append((press[0], press[1] or cut, None, line))
    if _CLOSED.search(line):
        out.append(("unrepeatable", cut, None, line))
    return out


def _release_reads(release) -> list:
    """A release not yet out, off `proof_release.reading`'s `waiting` lines."""
    if release is None or getattr(release, "state", None) != "waiting":
        return []
    out = []
    for line in getattr(release, "lines", None) or []:
        if not str(line).startswith("waiting"):
            continue
        tag = _RELEASE_TAG.match(line)
        out.append(("release", next((g for g in tag.groups() if g), None) if tag
                    else proof_record._cut(line, CUT), None, line))
    return out


def _clock(card: dict) -> datetime | None:
    """The earliest of the first run and the open record, else the lane entry."""
    started = [t for t in (_time(card.get("first_run")), _time(card.get("record_opened"))) if t]
    return min(started) if started else _time(card.get("entered"))


def diagnose(card: dict, holds: list, rows: list, *, now: datetime,
             release=None) -> Diagnosis | None:
    """The card's diagnosis, or None when it is not overdue. Pure.

    `card` is the card's facts (`facts`): `identifier`, `repo` (its slug),
    `lane`, `entered`, `first_run` and `record_opened` (times, or None),
    `states` — the state of each card its holds and rows name — and
    `blockers`, each card on an open `blocks` relation with its state. `holds` are
    its open `🔬 proof-waiting` lines (`proof_record.open_holds`), `rows` its
    record's (criterion, result) pairs (`proof_record.criterion_rows`), and
    `release` the release reading for a card never run, or None."""
    clock = _clock(card)
    if clock is None:
        raise ValueError(f"{card.get('identifier')} has no time to clock its age from")
    age = (now - clock).total_seconds() / 86400
    if age <= AGE_DAYS:
        return None
    unmet = _unmet(rows)
    behind = any(proof_record.row_waiting(r) for _, r in unmet)
    reads = [read for c, r in unmet for read in _row_reads(c, r, card)]
    reads += [read for line in holds or () for read in _hold_reads(line, card, behind=behind)]
    reads += [("release", i, None, f"blocked by {i} ({state})")
              for i, state in (card.get("blockers") or {}).items()
              if state not in prose_blockers.TERMINAL]
    reads += _release_reads(release)
    found = next((cls for cls in CLASSES if any(r[0] == cls for r in reads)), None)
    if found is None:
        if unmet:
            waits_on, evidence = proof_record._cut(unmet[0][0], CUT), unmet[0][1].strip()
        elif holds:
            line = _first_line(holds[0])
            waits_on, evidence = proof_record._cut(_observed(line), CUT), line
        else:
            waits_on, evidence = NOTHING_NAMED, ""
        cls, not_before, eliminated = "unrepeatable", None, True
    else:
        of = [r for r in reads if r[0] == found]
        pick = max(of, key=lambda r: r[2]) if found == "date" else of[0]
        cls, waits_on, not_before, evidence = pick
        eliminated = False
    return Diagnosis(card=card.get("identifier") or "", repo=card.get("repo") or "",
                     age_days=age, clock_from=clock, cls=cls, action=ACTIONS[cls],
                     waits_on=waits_on, not_before=not_before, by_elimination=eliminated,
                     evidence=evidence)


# --------------------------------------------------------------------------- #
# the saved card                                                               #
# --------------------------------------------------------------------------- #


def _labels(issue: dict) -> list:
    return [n.get("name") or "" for n in (issue.get("labels") or {}).get("nodes") or []]


def _slug(issue: dict) -> str | None:
    return next((name.split(":", 1)[1] for name in _labels(issue)
                 if name.lower().startswith("repo:")), None)


def _repo_map() -> dict:
    return json.loads(REPO_MAP.read_text(encoding="utf-8"))


def _full(slug: str | None) -> str:
    """The `owner/name` a slug routes to (`config/repo-map.json`)."""
    full = _repo_map().get(slug or "")
    if not full:
        raise LookupError(f"repo:{slug} is not a slug in config/repo-map.json")
    return full


def _voices(saved: dict) -> list:
    issue = saved.get("card") or {}
    return spoken_thread.voices(saved.get("thread") or [], saved.get("viewer"),
                                card=issue.get("identifier") or "")


def _entered(issue: dict) -> str | None:
    """When the card entered the lane it is in now: its newest move into it."""
    lane = (issue.get("state") or {}).get("name")
    moves = sorted(h.get("createdAt") or "" for h in (issue.get("history") or {}).get("nodes") or []
                   if ((h.get("toState") or {}).get("name")) == lane)
    return moves[-1] if moves else issue.get("createdAt")


def facts(saved: dict) -> dict:
    """The facts `diagnose` reads off a saved card (`gather`'s shape)."""
    issue = saved.get("card") or {}
    receipts = [_time(n.get("createdAt")) for n in saved.get("thread") or []
                if proof_run_state.receipt(n.get("body") or "") is not None]
    receipts = [t for t in receipts if t is not None]
    pr = (saved.get("record") or {}).get("pr") or {}
    opened = _time(pr.get("createdAt")) if card_pr.pr_state(pr) == card_pr.OPEN else None
    blockers = {}
    for rel in (issue.get("inverseRelations") or {}).get("nodes") or []:
        other = rel.get("issue") or {}
        state = (other.get("state") or {}).get("name")
        if (rel.get("type") == "blocks" and other.get("identifier")
                and state not in prose_blockers.TERMINAL):
            blockers[other["identifier"]] = state or "unread"
    return {"identifier": issue.get("identifier") or "",
            "repo": _slug(issue) or saved.get("repo") or "",
            "labels": _labels(issue), "lane": (issue.get("state") or {}).get("name"),
            "entered": _time(_entered(issue)), "first_run": min(receipts) if receipts else None,
            "record_opened": opened, "states": dict(saved.get("states") or {}),
            "blockers": blockers}


def _rows(saved: dict) -> list:
    text = (saved.get("record") or {}).get("text")
    return (proof_record.criterion_rows(text) or []) if text else []


def from_saved(saved: dict, now: datetime | None = None) -> Diagnosis | None:
    """`diagnose` over a saved card, at `now` — or, by default, at the time
    it was saved, so a copy of the board replays as it stood."""
    now = now or _time(saved.get("saved_at"))
    if now is None:
        raise ValueError("the saved card says no time it was saved at")
    release = saved.get("release")
    if release is not None:
        release = proof_release.Reading(release.get("state"), list(release.get("lines") or []))
    return diagnose(facts(saved), proof_record.open_holds(_voices(saved)), _rows(saved),
                    now=now, release=release)


# --------------------------------------------------------------------------- #
# the reads                                                                    #
# --------------------------------------------------------------------------- #

#: The card, its lane's history and its epic's children, in one read.
CARD_QUERY = """query($id: String!) { issue(id: $id) {
             identifier title createdAt description state { name }
             labels { nodes { name } }
             history(first: 100) { nodes { createdAt toState { name } } }
             inverseRelations(first: 100) { nodes { type issue { identifier state { name } } } }
             parent { identifier children(first: 100) { nodes {
               identifier title state { name } labels { nodes { name } } } } }
           } }"""
#: A repo's open PROOF cards, in every open lane.
PROOFS_QUERY = """query($label: String!) { issues(first: 100, filter: {
             team: {key: {eq: "DRE"}}, title: {startsWith: "PROOF:"},
             labels: {name: {eqIgnoreCase: $label}},
             state: {type: {nin: ["completed", "canceled"]}}}) { nodes { identifier } } }"""
#: What a saved card keeps of its record pull request.
PR_KEPT = ("number", "state", "createdAt", "headRefName", "headRefOid", "files")
#: The fields of the record branch's pull requests, closed ones too.
BRANCH_PR_FIELDS = "number,state,createdAt,headRefName,headRefOid,files"
#: The record pull request's fields: what the record and the clock are read
#: off — `proof_dispatch.RECORD_FIELDS` without the comments and checks only
#: a re-run reads.
RECORD_PR_FIELDS = f"url,{BRANCH_PR_FIELDS}"


class LinearReads:
    """The four Linear reads this module makes, and nothing else."""

    def proofs(self, slug: str) -> list:
        data = linear_ops.gql(PROOFS_QUERY, {"label": f"repo:{slug}"}) or {}
        return [n["identifier"] for n in ((data.get("issues") or {}).get("nodes") or [])
                if n.get("identifier")]

    def card(self, identifier: str) -> dict:
        issue = (linear_ops.gql(CARD_QUERY, {"id": identifier}) or {}).get("issue")
        if not issue:
            raise LookupError(f"Linear answered no card for {identifier}")
        return issue

    def thread(self, identifier: str):
        return _dispatch().LinearReads().thread(identifier)

    def states(self, identifiers: list) -> dict:
        return _dispatch().LinearReads().states(identifiers)


def record_path(files) -> str | None:
    """The record a pull request carries, for diagnosis: the one `.md` file
    it ADDS under `proof_record.RECORD_DIRS` (`proof_record.find_record`), or
    else the one it MODIFIES there — a shape the gate still refuses."""
    path, _ = proof_record.find_record(files)
    if path is not None or not isinstance(files, list):
        return path
    modified = [f["path"] for f in files
                if isinstance(f, dict) and isinstance(f.get("path"), str)
                and f["path"].startswith(proof_record.RECORD_DIRS) and f["path"].endswith(".md")
                and f.get("changeType") == "MODIFIED"]
    return modified[0] if len(modified) == 1 else None


def _kept(pr: dict | None) -> dict | None:
    if not pr:
        return None
    return {key: pr.get(key) for key in PR_KEPT}


def _files_line(description: str) -> str | None:
    """The one record the card's `**Files:**` line names, or None."""
    try:
        named = plan_footprint.declared_files(description or "")
    except plan_footprint.FootprintMissing:
        return None
    records = sorted(p for p in named if p.startswith(proof_record.RECORD_DIRS)
                     and p.endswith(".md"))
    return records[0] if len(records) == 1 else None


def _record(identifier: str, full: str, description: str, gh) -> dict:
    """The record, read on its open pull request at the head — as
    `proof_dispatch.record_pr` and `record_at` read it — or on its branch at
    its head. A branch GitHub says is gone is no record; any other failed read
    raises, and the card is named as not read."""
    dispatch = _dispatch()
    branch = f"agent/{identifier}-proof-record"
    pr = card_pr.find(identifier, branch=branch, repo=full, fields=RECORD_PR_FIELDS, run=gh)
    if pr and card_pr.pr_state(pr) == card_pr.OPEN:
        head, path = pr.get("headRefOid"), record_path(pr.get("files"))
    else:
        try:
            ref = json.loads(gh(["api", f"repos/{full}/git/ref/heads/{branch}"]) or "null")
        except Exception as error:  # noqa: BLE001 — a 404 is GitHub's answer
            if dispatch._NOT_FOUND.search(str(error)):
                return {"pr": _kept(pr), "branch": branch, "head": None, "path": None,
                        "text": None}
            raise
        head = ((ref or {}).get("object") or {}).get("sha")
        listed = json.loads(gh(["pr", "list", "--repo", full, "--head", branch, "--state", "all",
                                "--json", BRANCH_PR_FIELDS]) or "[]")
        newest = card_pr.newest([p for p in listed if isinstance(p, dict)])
        pr = pr or newest
        path = record_path((newest or {}).get("files")) or _files_line(description)
    text = proof_record.record_text(full, path, head, gh) if path and head else None
    return {"pr": _kept(pr), "branch": branch, "head": head, "path": path, "text": text}


def _release(issue: dict, identifier: str, slug: str, full: str, gh) -> dict | None:
    """The release reading for a card never run, over its siblings' merges in
    this repo — condition 7's reading (`proof_dispatch`), or None with no
    merge to read."""
    dispatch = _dispatch()
    merges = []
    for sibling in ((issue.get("parent") or {}).get("children") or {}).get("nodes") or []:
        other = sibling.get("identifier")
        if (other == identifier or proof_and_demo.is_proof(sibling.get("title") or "")
                or (sibling.get("state") or {}).get("name") not in prose_blockers.TERMINAL
                or _slug(sibling) != slug):
            continue
        pr = card_pr.find(other, repo=full, fields=dispatch.PR_FIELDS, run=gh)
        sha = ((pr or {}).get("mergeCommit") or {}).get("oid")
        if card_pr.pr_state(pr) == card_pr.MERGED and sha:
            merges.append(proof_release.Merge(other, pr["number"], sha, []))
    if not merges:
        return None

    def read(path: str):
        try:
            text = gh(["api", path])
        except Exception as error:  # noqa: BLE001 — told apart as the reader tells them
            raise (proof_release.Missing if "HTTP 404" in str(error) else RuntimeError)(
                str(error)) from error
        return json.loads(text) if text else None

    got = proof_release.reading(full, merges, read=read)
    return {"state": got.state, "lines": list(got.lines)}


def gather(card: str, *, now: datetime, gh=None, linear=None) -> dict:
    """The card as it stands, saved in the shape `from_saved` judges: its
    issue, thread and viewer, its record, the states of the cards its holds
    and rows name, and its release reading when it never ran. Read-only."""
    gh = gh or proof_record._gh
    linear = linear or LinearReads()
    issue = linear.card(card)
    slug = _slug(issue)
    full = _full(slug)
    nodes, viewer = linear.thread(card)
    saved = {"saved_at": _utc(now), "repo": slug, "card": issue, "thread": nodes,
             "viewer": viewer, "record": _record(card, full, issue.get("description"), gh),
             "states": {}, "release": None}
    named = [i for line in proof_record.open_holds(_voices(saved)) for i in _IDS.findall(line)]
    named += [i for _, result in _unmet(_rows(saved)) for i in _IDS.findall(result)]
    named = [i for i in dict.fromkeys(i.upper() for i in named) if i != card]
    saved["states"] = dict(linear.states(named)) if named else {}
    if facts(saved)["first_run"] is None:
        saved["release"] = _release(issue, card, slug, full, gh)
    return saved


def reading(card: str, *, now: datetime, gh=None, linear=None) -> Diagnosis | None:
    """`diagnose` over the card as it stands, reading its holds, its record
    and its release itself — read-only. `gh` is `gh <args>`'s stdout,
    raising on a failed read; `linear` serves `card`, `thread` and `states`."""
    return from_saved(gather(card, now=now, gh=gh, linear=linear), now)


# --------------------------------------------------------------------------- #
# the replay                                                                   #
# --------------------------------------------------------------------------- #


def _why(error) -> str:
    return " ".join(str(error or "").split())[:300] or type(error).__name__


def _replay_fixture(path: str, now: datetime | None) -> int:
    try:
        saved = json.loads(Path(path).read_text(encoding="utf-8"))
        slug = saved.get("repo") or _slug(saved.get("card") or {})
        os.environ.setdefault("REPO", _full(slug))
        got = from_saved(saved, now)
    except Exception as error:  # noqa: BLE001 — named, never skipped
        print(f"{TAG} {path} · could not be read — {_why(error)}")
        return 1
    print(got.line() if got else f"{TAG} none overdue in {slug}")
    return 0


def _replay_repo(slug: str, *, linear, gh, now: datetime) -> int:
    try:
        os.environ.setdefault("REPO", _full(slug))
        cards = linear.proofs(slug)
    except Exception as error:  # noqa: BLE001
        print(f"{TAG} the open PROOF cards of {slug} could not be read — {_why(error)}")
        return 1
    overdue, failed = 0, False
    for card in cards:
        try:
            got = reading(card, now=now, gh=gh, linear=linear)
        except Exception as error:  # noqa: BLE001 — named on its own line
            print(f"{TAG} {card} · could not be read — {_why(error)}")
            failed = True
            continue
        if got is not None:
            print(got.line())
            overdue += 1
    if not overdue and not failed:
        print(f"{TAG} none overdue in {slug}")
    return 1 if failed else 0


def main(argv=None, *, linear=None, gh=None, now: datetime | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="cmd", required=True)
    cmd = sub.add_parser("replay", help="print one line per overdue PROOF card, read-only")
    source = cmd.add_mutually_exclusive_group(required=True)
    source.add_argument("--repo", help="a slug of config/repo-map.json: its open PROOF cards")
    source.add_argument("--fixture", help="a saved card (gather's shape) instead of the board")
    args = parser.parse_args(argv)
    if args.fixture:
        return _replay_fixture(args.fixture, now)
    return _replay_repo(args.repo, linear=linear or LinearReads(), gh=gh or proof_record._gh,
                        now=now or datetime.now(timezone.utc))


if __name__ == "__main__":
    sys.exit(main())
