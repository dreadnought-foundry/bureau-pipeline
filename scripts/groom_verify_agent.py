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
    verify, its title, body and board context — age, labels, parent,
    children, last move, and every comment with who said it — read through
    `linear_ops` in one request and passed through `sanitize_untrusted`, its
    repo mapped through `config/repo-map.json`; and the matrix the workflow
    fans out over. It also decides which cards are EXCLUDED without
    judgement (DRE-5306): a parent epic with an open child, a `hand-built`
    card, a card moved into Intake from another lane in the last
    `EXCLUDE_DAYS` days, a card whose board context could not be read, and
    (DRE-5746) a card whose repo is not a key of `config/repo-map.json`.
    An excluded card gets no agent, its verdict is `excluded`, and `apply`
    drops it from the batch and leaves it where it is on the board. And each
    card's `lookups` block, through `groom_lookups.cards` (DRE-5458): the
    lookup state it leads to is read first in `judge`, carried on every
    verdict and every mark, and a mapped card whose own repo did not answer
    is `unverified` with `lookup failed: <why>` whatever the agent said.
  * `prepare` — in the verify job, with no Linear key: the agent's whole
    input, and the moment it started.
  * `verdict` — always, even when the agent step died or never ran: the raw
    answer, checked, in the fixed shape the proposal reads.
  * `apply` — back in the groom job: a Planning card proved `done-elsewhere`,
    `obsolete` or `not-worth-it` goes on the Cancel list with the proof as its reason, the next spare
    still needed joins the Planning list, the list is re-ordered by the
    rules' order and renumbered from 1 (DRE-5858), and the proposal id is
    recomputed — except
    an epic with an open child, which keeps its place and is listed in
    `cancels_refused` (DRE-5309). And it
    decides whether the morning is posted at all (DRE-5317): the lookup
    failed for every card that names a file, or the pre-post check searched
    no owner for merged pull requests, and `not_posted_why` says so —
    `groomer.py post` reads it and posts no proposal.

**A run that dies lands as `unverified`, never as `still-needed`.** Anything
but a well-formed answer with the proof the brief requires, from a step that
succeeded, is `unverified` with the reason named; so is a target whose verdict
artifact never arrived.

**No code read, no verdict.** A card whose repo is not a key of
`config/repo-map.json` has no repository to check out, so no agent ever reads
code for it — and since DRE-5746 it is the fifth exclusion, so it leaves the
Planning list the way the other four do rather than sitting on it as work the
fleet cannot build. Every subcommand holds that on its own rather than
trusting the workflow to skip the card: `targets` excludes it, `prepare`
refuses to write an agent input for it, `verdict` forces `excluded` whatever
the raw answer says, and `apply` does the same with or without a verdict
file.

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
import re
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import execution_result  # noqa: E402 — the one loader of the execution file
import groom_context  # noqa: E402 — the pack's sections, for the stop's suffix
import groom_lookups  # noqa: E402 — the lookups before the agent (DRE-5458)
import groom_verify  # noqa: E402 — the spare count and source names
import groomer  # noqa: E402 — proposal_id, assert_disjoint
import linear_ops  # noqa: E402 — the Linear read `targets` makes
import planning_classify  # noqa: E402 — which model answered
import sanitize_untrusted  # noqa: E402 — the fence, made mechanical
import spoken_thread  # noqa: E402 — the one reader of who said a comment

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
#: Every reason an `unverified` mark may carry, beside `lookup failed: <why>`
#: (`groom_lookups.LOOKUP_FAILED`). Nothing else reaches the page (DRE-5746):
#: `_mark` reads any other as `unreadable answer`.
UNVERIFIED_REASONS = (STEP_FAILED, STEP_SKIPPED, NO_FILE, NO_PROOF,
                      UNREADABLE, NO_ARTIFACT)
#: The fifth exclusion reason (DRE-5746), exact.
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

#: The lookup state every verdict carries — `groom_lookups.LOOKUP_STATES`.
(_LOOKUP_OK, _LOOKUP_FAILED, _LOOKUP_NONE,
 _LOOKUP_NOT_RUN) = groom_lookups.LOOKUP_STATES

FENCE_BEGIN = "===== BEGIN UNTRUSTED CARD TEXT ====="
FENCE_END = "===== END UNTRUSTED CARD TEXT ====="
#: The section `agent_input` writes after the card's body (DRE-5306).
CONTEXT_HEADING = "## The card's board context"

#: The card and its board context, in ONE request per card (DRE-5306): each
#: list bounded at fifty, so a request asks for at most 150 nested nodes.
CARD_QUERY = """query($id: String!) {
  issue(id: $id) {
    identifier title description createdAt
    labels { nodes { name } }
    parent { identifier state { name } }
    children(first: 50) { nodes { identifier state { name } } }
    comments(first: 50) { nodes { body createdAt user { id } botActor { id } } }
    history(first: 50) { nodes { createdAt fromState { name } toState { name } actor { id } botActor { id } } }
  }
}"""

#: How recent a move into Intake keeps a card out of the morning's judgement.
EXCLUDE_DAYS = 7
#: `board context unread: <why>` when Linear would not say who the pipeline's
#: own key is — exact; DRE-5307 names its own unread cards with it.
VIEWER_UNREAD = "the pipeline's own Linear identity could not be read"
#: The exclusion reasons, exact (DRE-5306) — four read off the card, and the
#: fifth, `UNMAPPED`, off the repo map (DRE-5746). Decided in `targets`,
#: never by the model.
EXCLUDE_EPIC = "parent epic with {n} open {children}"
#: The label `reconcile.HAND_BUILT_LABEL` names: a person builds this card.
HAND_BUILT = "hand-built"
EXCLUDE_MOVED = "moved into Intake on {day}"
EXCLUDE_UNREAD = "board context unread: {why}"
_EXCLUSION_RE = re.compile(
    r"(parent epic with [1-9]\d* open (child|children)|hand-built"
    r"|moved into Intake on \d{4}-\d{2}-\d{2}|board context unread: \S.*"
    r"|repo not in config/repo-map\.json: \S+)")
INTAKE = "Intake"
#: `prepare`'s whole output for an excluded card, and the prefix of the
#: `not-now` and `sequence` reason `apply` writes for one.
EXCLUDED_PREFIX = "excluded without judgement: "

#: A comment's `by`: `spoken_thread`'s voice kind, in the words the agent
#: reads. A withheld comment's text never reaches the agent.
BY_VOICE = {spoken_thread.CEO_VIA_CONSOLE: "ceo",
            spoken_thread.PERSON: "person",
            spoken_thread.PIPELINE: "pipeline",
            spoken_thread.INTEGRATION: "integration",
            spoken_thread.UNKNOWN: "unknown",
            spoken_thread.REFUSED: "withheld",
            spoken_thread.UNCHECKED: "withheld"}
WITHHELD = "withheld"

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


def is_exclusion(reason) -> bool:
    """Is this one of the five exclusion reasons, in its exact shape?"""
    return isinstance(reason, str) and bool(_EXCLUSION_RE.fullmatch(reason))


def is_unverified_reason(reason) -> bool:
    """Is this a reason an `unverified` mark may carry — one of
    `UNVERIFIED_REASONS`, or `lookup failed: <why>`?"""
    if not isinstance(reason, str):
        return False
    if reason.startswith(groom_lookups.LOOKUP_FAILED):
        return bool(reason[len(groom_lookups.LOOKUP_FAILED):].strip())
    return reason in UNVERIFIED_REASONS


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


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _clean(text) -> str:
    return sanitize_untrusted.sanitize_line(str(text or ""))


def _state(node) -> str:
    return ((node or {}).get("state") or {}).get("name") or ""


def _nodes(issue: dict, field: str) -> list[dict]:
    return [n for n in ((issue.get(field) or {}).get("nodes") or [])
            if isinstance(n, dict)]


def _by_key(node: dict, viewer: str) -> str:
    """Whose key made a state move — read off the actor alone. A move carries
    no console receipt, so a console move for the CEO reads `pipeline`."""
    actor = (node.get("actor") or {}).get("id")
    if not actor:
        return spoken_thread.UNKNOWN
    return spoken_thread.PIPELINE if actor == viewer else spoken_thread.PERSON


def _moves(issue: dict) -> list[tuple[datetime, dict]]:
    """Every move from one lane to another, newest first. An entry with no
    `fromState` is the card's creation, never a move."""
    found = []
    for node in _nodes(issue, "history"):
        at = _moment(node.get("createdAt"))
        if (at and (node.get("fromState") or {}).get("name")
                and (node.get("toState") or {}).get("name")):
            found.append((at, node))
    return sorted(found, key=lambda pair: pair[0], reverse=True)


def board_context(issue: dict, viewer: str, *, card: str, now: datetime,
                  verifier=None) -> dict:
    """The card's board context, every string through `sanitize_untrusted`.
    Who said each comment is `spoken_thread.voices`'s answer, and a comment
    it withholds is shown by its label alone."""
    created = _moment(issue.get("createdAt"))
    if created is None:
        raise ValueError("Linear gave no readable createdAt for the card")
    moves = _moves(issue)
    last = None
    if moves:
        at, node = moves[0]
        last = {"at": _clean(node.get("createdAt")),
                "to": _clean(node["toState"]["name"]),
                "by": _by_key(node, viewer)}
    comments = sorted(_nodes(issue, "comments"),
                      key=lambda n: _moment(n.get("createdAt"))
                      or datetime.min.replace(tzinfo=timezone.utc))
    said = []
    for voice in spoken_thread.voices(comments, viewer, card=card,
                                      verifier=verifier):
        by = BY_VOICE.get(voice.kind, spoken_thread.UNKNOWN)
        said.append({"at": _clean(voice.created_at), "by": by,
                     "body": (_clean(voice.label) if by == WITHHELD
                              else sanitize_untrusted.sanitize_body(
                                  voice.body or ""))})
    parent = issue.get("parent")
    return {
        "created_at": _clean(issue.get("createdAt")),
        "age_days": max(0, (now - created).days),
        "labels": [_clean(n.get("name")) for n in _nodes(issue, "labels")],
        "parent": ({"identifier": _clean(parent.get("identifier")),
                    "state": _clean(_state(parent))}
                   if isinstance(parent, dict) else None),
        "children": [{"identifier": _clean(n.get("identifier")),
                      "state": _clean(_state(n))}
                     for n in _nodes(issue, "children")],
        "last_moved": last,
        "comments": said,
    }


def exclusion(issue: dict, *, now: datetime) -> str | None:
    """Why the card sits out the morning's judgement, or None. Decided here,
    over what Linear said, and never by the model."""
    n = groomer.open_children(issue)
    if n:
        return EXCLUDE_EPIC.format(n=n, children="child" if n == 1
                                   else "children")
    if HAND_BUILT in {label.get("name") for label in _nodes(issue, "labels")}:
        return HAND_BUILT
    # The newest move INTO Intake from another lane, whoever's key made it:
    # nothing can tell the console's move for the CEO from a workflow's.
    into = [at for at, node in _moves(issue)
            if node["toState"]["name"] == INTAKE]
    if into and now - into[0] <= timedelta(days=EXCLUDE_DAYS):
        return EXCLUDE_MOVED.format(day=into[0].strftime("%Y-%m-%d"))
    return None


def _viewer(lops) -> str | None:
    try:
        return lops.viewer_id() or None
    except Exception:  # noqa: BLE001 — an unread viewer is said, not fatal
        return None


def targets(proposal: dict, *, lops, repo_map: dict,
            verifier=None) -> list[dict]:
    """Every card to verify, read with its board context in one request
    each, and the reason it is excluded without judgement or None.

    The pipeline's own identity is read once, before any card. Without it
    nobody can be told apart from the pipeline, so no card is read and every
    one is excluded as unread — nothing is judged on a guess."""
    rows = {r["identifier"]: r for r in proposal["sequence"]}
    cards = window(proposal)
    viewer = _viewer(lops) if cards else None
    now = _utcnow()
    out = []
    for identifier, which in cards:
        row = rows.get(identifier) or {}
        repository, slug = _repo(row, repo_map)
        target = {"card": identifier, "repository": repository,
                  "repo_slug": slug, "title": "", "body": "",
                  "evidence": _layer_a(proposal, identifier), "list": which,
                  "context": None, "excluded": None}
        out.append(target)
        if viewer is None:
            target["excluded"] = EXCLUDE_UNREAD.format(why=VIEWER_UNREAD)
            continue
        try:
            issue = (lops.gql(CARD_QUERY, {"id": identifier}) or {}).get("issue")
            if not isinstance(issue, dict):
                raise ValueError("Linear returned no card")
            context = board_context(issue, viewer, card=identifier, now=now,
                                    verifier=verifier)
        except Exception as e:  # noqa: BLE001 — an unread card is excluded, not fatal
            # No title off the proposal row: a card with no children, labels
            # or history read would pass for a judgeable one.
            target["excluded"] = EXCLUDE_UNREAD.format(
                why=_clean(_one_line(e)) or type(e).__name__)
            continue
        # The fifth exclusion (DRE-5746): no repo in the map, no code to
        # read, and no place on the Planning list for work the fleet cannot
        # build. Decided here, off the map, never by the model.
        target.update(
            title=_clean(issue.get("title")),
            body=sanitize_untrusted.sanitize_body(issue.get("description") or ""),
            context=context,
            excluded=(unmapped_reason(target) if repository is None
                      else exclusion(issue, now=now)))
    # The newer cards naming each card's files (DRE-5458): one more request
    # per card that names a file and is not excluded, none for any other.
    groom_lookups.cards(out, lops=lops, now=now)
    return out


def matrix(rows: list[dict]) -> list[dict]:
    """The workflow's matrix: the empty string, not null, marks a card no
    agent reads — an unmapped repo or an excluded card — so
    `matrix.repository != ''` reads it."""
    return [{"card": r["card"],
             "repository": "" if r.get("excluded")
             else r.get("repository") or ""}
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


def _context_lines(context) -> list[str]:
    """The board-context section's fenced lines. Comments are text a person
    wrote, so all of it sits inside the fence."""
    if not isinstance(context, dict):
        return ["Not read this run."]

    def line(text) -> str:
        return _fenced(_one_line(text))

    parent = context.get("parent")
    moved = context.get("last_moved")
    lines = [f"Age: {context.get('age_days')} days "
             f"(created {line(context.get('created_at'))})",
             "Labels: " + (", ".join(line(label) for label
                                     in context.get("labels") or []) or "none"),
             "Parent: " + (f"{line(parent.get('identifier'))} "
                           f"({line(parent.get('state'))})"
                           if isinstance(parent, dict) else "none")]
    children = context.get("children") or []
    lines.append("Children:" + ("" if children else " none"))
    lines += [f"- {line(c.get('identifier'))} ({line(c.get('state'))})"
              for c in children]
    lines.append("Last state move: " + (
        f"to {line(moved.get('to'))} on {line(moved.get('at'))}, "
        f"by: {line(moved.get('by'))}" if isinstance(moved, dict) else "none"))
    comments = context.get("comments") or []
    lines.append("Comments, oldest first:" + ("" if comments else " none"))
    for comment in comments:
        lines.append(f"- {line(comment.get('at'))}, by: {line(comment.get('by'))}")
        body = _fenced(str(comment.get("body") or "")).strip("\n")
        lines += [f"  {text}" if text else "" for text in body.split("\n")]
    return lines


def _body(text: str) -> str:
    """The card's body, fenced — and a line mimicking the board-context
    heading defanged, since the real section follows it inside the same
    fence and a body must not be able to write a second one."""
    return "\n".join(
        sanitize_untrusted.DEFANG_PREFIX + line
        if line.strip().lower().startswith(CONTEXT_HEADING.lower()) else line
        for line in _fenced(text).split("\n"))


def agent_input(row: dict, brief: str) -> str:
    card = row["card"]
    evidence = row.get("evidence") or []
    # The board context sits inside the card's own fence, after the body:
    # comments are text a person wrote, and none of it is an instruction.
    lines = [brief.rstrip("\n"), "", "## The card", "", FENCE_BEGIN,
             f"Card: {card}",
             f"Title: {_fenced(sanitize_untrusted.sanitize_line(row.get('title') or ''))}",
             "", _body(row.get("body") or ""), "", CONTEXT_HEADING, ""]
    lines += _context_lines(row.get("context"))
    lines += [FENCE_END, "", "## The Layer A evidence", "", FENCE_BEGIN]
    lines += ([f"- {_fenced(_one_line(e))}" for e in evidence]
              or ["None recorded."])
    lines += [FENCE_END, "", "## The verdict file", "",
              SHAPE.replace("{card}", card), ""]
    return "\n".join(lines)


def prepare(rows: list[dict], card: str, *, brief: str) -> str:
    row = _target(rows, card)
    if row.get("excluded"):
        return EXCLUDED_PREFIX + _one_line(row["excluded"]) + "\n"
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


def _lookup_state(row: dict) -> str:
    """The row's lookup state (DRE-5458): `none` for a card that names no
    file or is excluded, `failed` or `ok` once `fold` decided it, and
    `not-run` when no `fold` ran."""
    look = row.get("lookups")
    if (row.get("excluded") or not isinstance(look, dict)
            or look.get("looked_up") is not True):
        return _LOOKUP_NONE
    if look.get("ok") is False:
        return _LOOKUP_FAILED
    return _LOOKUP_OK if look.get("ok") is True else _LOOKUP_NOT_RUN


def judge(raw_path, *, card: str, row: dict, outcome: str) -> dict:
    """`{verdict, summary, proof, reason, lookup}` — the answer as the
    proposal may read it. The target row first, then the step, then the
    file."""
    # The lookup state FIRST, and on every answer — the unmapped one too, so
    # a morning of unmapped cards still counts in DRE-5317's stop.
    lookup = _lookup_state(row)

    def unverified(reason: str, summary: str) -> dict:
        return {"verdict": UNVERIFIED, "summary": summary, "proof": [],
                "reason": reason, "lookup": lookup}

    # Off the ROW, before anything else: `targets` decided it, and no raw
    # answer — and no step outcome — can change it.
    if row.get("excluded"):
        return {"verdict": EXCLUDED, "proof": [],
                "summary": "Not judged: the card was excluded before any "
                           "agent read it.",
                "reason": _one_line(row["excluded"]), "lookup": lookup}
    # A row written before DRE-5746 made the unmapped repo an exclusion in
    # `targets`: the same answer here, so no older targets file can put it
    # back on the Planning list.
    if _is_unmapped(row):
        return {"verdict": EXCLUDED, "proof": [],
                "summary": "Not judged: the card's repo is not one the "
                           "pipeline can check out.",
                "reason": unmapped_reason(row), "lookup": lookup}
    if lookup == _LOOKUP_FAILED:
        return unverified(
            groom_lookups.LOOKUP_FAILED + _one_line(row["lookups"].get("why")),
            "Not judged: the lookup in the card's own repo did not answer, "
            "so the agent's answer was not used.")
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
            "reason": None, "lookup": lookup}


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
    wall clock is read from, and the lookup state on every mark (DRE-5458):
    the document's, else `none` for a row excluded, else `not-run` — an
    unknown never reads as a failure."""
    found = doc.get("lookup") if isinstance(doc, dict) else None
    lookup = (found if found in groom_lookups.LOOKUP_STATES
              else _LOOKUP_NONE if row.get("excluded") else _LOOKUP_NOT_RUN)
    blank = {"summary": None, "proof": [], "cost_usd": None,
             "duration_ms": None, "model": None, "started_at": None,
             "finished_at": None, "lookup": lookup}
    if row.get("excluded"):
        return {**blank, **_spend_of(doc), "verdict": EXCLUDED,
                "reason": _one_line(row["excluded"])}
    # `apply` in the workflow has no targets file, so a document saying
    # `excluded` is how it learns — believed only with one of the five
    # reasons, and never over a row that says the card was not excluded.
    if (isinstance(doc, dict) and doc.get("verdict") == EXCLUDED
            and "excluded" not in row and is_exclusion(doc.get("reason"))):
        return {**blank, **_spend_of(doc), "verdict": EXCLUDED,
                "summary": doc.get("summary"),
                "reason": _one_line(doc["reason"])}
    if _is_unmapped(row):
        return {**blank, **_spend_of(doc), "verdict": EXCLUDED,
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
    elif mark["verdict"] == UNVERIFIED and not is_unverified_reason(
            mark["reason"]):
        # Only the exact reasons reach the page (DRE-5746).
        mark.update(reason=UNREADABLE, proof=[])
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
    parts += [groomer.proof_line(item) for item in mark.get("proof") or []]
    return groomer.defang_reason("; ".join(parts))[0]


def _excluded_fields(mark: dict) -> dict:
    """What a `not-now` or `sequence` row of an excluded card says."""
    return {"reason": EXCLUDED_PREFIX + _one_line(mark.get("reason")),
            "cycle": None, "cycle_id": None, "projected": False,
            "trigger": None, "evidence": None, "judged": False, "reasons": {}}


def _verify_field(mark: dict) -> dict:
    field = {k: mark.get(k) for k in ("verdict", "summary", "proof", "reason",
                                      "cost_usd", "duration_ms", "model")}
    # Only on a Cancel the guard refused (DRE-5309), so every other row keeps
    # the shape it had.
    if mark.get("refused"):
        field["refused"] = mark["refused"]
    return field


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

    # The cancel guard (DRE-5309): an epic with an open child is never a
    # Cancel, whatever the agent proved about its text. A refused card keeps
    # the place it had — its Planning slot, or its wait in `not-now`.
    refused = proposal.setdefault("cancels_refused", [])

    def refuse(identifier: str) -> bool:
        if marks[identifier]["verdict"] not in CANCELS:
            return False
        refusal = groomer.cancel_refusal(
            {"identifier": identifier, **(by_seq.get(identifier) or {})})
        if refusal is None:
            return False
        marks[identifier]["refused"] = refusal
        refused.append({"identifier": identifier, "refusal": refusal,
                        "source": CANCEL_SOURCE})
        return True

    canceled: list[str] = []
    promoted: dict[str, dict] = {}
    kept, unfilled = [], 0
    for slot in now:
        identifier = slot["identifier"]
        verdict_ = marks.get(identifier, {}).get("verdict")
        if (verdict_ not in CANCELS and verdict_ != EXCLUDED
                or identifier in marks and refuse(identifier)):
            kept.append(slot)
            continue
        # An excluded card leaves the Planning list by the same walk a
        # Cancel takes, and goes on no other list: nothing moves it.
        if verdict_ != EXCLUDED:
            canceled.append(identifier)
        taker = next(queue, None)
        if taker is None:
            # `slots_unfilled` counts what a Cancel emptied, as the page
            # says; the excluded card is named under its own heading.
            if verdict_ != EXCLUDED:
                unfilled += 1
            continue
        promoted[taker] = slot
        base = by_seq.get(taker) or waiting[taker]
        row = {k: base.get(k) for k in _NOW_FIELDS}
        row.update(identifier=taker, position=slot["position"],
                   cycle=slot.get("cycle"), cycle_id=slot.get("cycle_id"))
        if base.get("held"):
            row["held"] = True
        row.update(trigger=None, evidence=None, judged=False, reasons={})
        kept.append(row)
    # A spare proved done elsewhere, obsolete or not worth it is canceled
    # the same way, in order.
    canceled += [i for i in spare_ids
                 if marks[i]["verdict"] in CANCELS and i not in promoted
                 and not refuse(i)]

    # The list is in the rules' order (DRE-5858): each card's place in the
    # sequence `propose` walked, never the slot a spare inherited — so a
    # promoted spare follows every card the rules placed, and is numbered
    # from 1 with them. The rules' reason names that number, so it is
    # rewritten after it — on a promoted spare, and on any card that moved
    # up. A reason the rules did not write names no number and is kept.
    kept.sort(key=lambda r: (by_seq.get(r["identifier"]) or r)["position"])
    for n, row in enumerate(kept, 1):
        ours = (row["identifier"] in promoted
                or row.get("reason") == groomer._rules_reason("now", row))
        row["position"] = n
        if ours:
            row["reason"] = groomer._rules_reason("now", row)
    outcomes["now"] = kept
    gone = set(canceled) | set(promoted)
    outcomes["not-now"] = [r for r in outcomes["not-now"]
                           if r["identifier"] not in gone]
    # Every excluded card, Planning or spare, waits in `not-now` with the
    # reason and nothing scheduled: no cycle, and no trigger for "Not now"
    # to invent a plan from. It stays in Intake.
    excluded = [r["card"] for r in rows if marks[r["card"]]["verdict"] == EXCLUDED]
    waiting_now = {r["identifier"]: r for r in outcomes["not-now"]}
    for identifier in excluded:
        row = waiting_now.get(identifier)
        if row is None:
            base = by_seq.get(identifier) or {}
            row = {"identifier": identifier, "title": base.get("title"),
                   "repo": base.get("repo"), "older_than_window": False}
            outcomes["not-now"].append(row)
        row.update(_excluded_fields(marks[identifier]),
                   reconsidered_in=None)
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
        elif identifier in excluded:
            row.update(_excluded_fields(marks[identifier]), outcome="not-now")

    for rows_ in (outcomes["now"], outcomes["not-now"], outcomes["dead"], seq):
        for row in rows_:
            if row["identifier"] in marks:
                row["verify"] = _verify_field(marks[row["identifier"]])

    proposal["batch"]["cards"] = len(kept)
    proposal["deprioritised"] = groomer._deprioritised(proposal)
    order = [r["card"] for r in rows]
    proposal["verify"] = {**summary(marks, order, unfilled),
                          **stop(proposal, marks, order, repo_map)}
    groomer.assert_disjoint(proposal)
    proposal["id"] = groomer.proposal_id(proposal)
    return proposal


#: The two reasons a morning posts no proposal (DRE-5317), and the clause
#: added when the header's merged-PR count failed too.
ALL_LOOKUPS_FAILED = "the lookup failed for every card: "
MERGED_PRS_UNREAD = ("no reader could see merged pull requests this morning: "
                     "the check searched no owner — ")
COUNT_UNREAD_TOO = "; the merged-PR count was unread too"


def _count_unread(proposal: dict, repo_map: dict) -> bool:
    """Was the header's merged-PR count — the other 2026-09-29 reader —
    asked for, and did it fail? A `--no-judgement` run never asked, and a
    pack with every section unread is the shape of one that read no pack
    (`groomer._EMPTY_PACK`), so neither says the count failed."""
    block = proposal.get("judgement") or {}
    if block.get("enabled") is not True:
        return False
    unread = set((block.get("pack") or {}).get("unread") or ())
    if set(groom_context.SECTIONS) <= unread:
        return False
    if "merged_prs" in unread:
        return True
    owners = {str(full).split("/")[0] for full in (repo_map or {}).values()}
    return bool(owners) and all(f"merged_prs:{o}" in unread for o in owners)


def stop(proposal: dict, marks: dict[str, dict], order: list[str],
         repo_map: dict) -> dict:
    """The four keys `groomer.py post` and the receipt read (DRE-5317).

    Read off the marks and the verification record, never the target rows:
    the workflow's apply step has none. A mark with lookup `none` and an
    excluded mark count on neither side, so one no-file card can neither
    fire the stop nor hold it off.
    """
    judged = [i for i in order if marks[i]["verdict"] != EXCLUDED]
    failed_ = [i for i in judged if marks[i].get("lookup") == _LOOKUP_FAILED]
    all_failed = bool(failed_) and not any(
        marks[i].get("lookup") in (_LOOKUP_OK, _LOOKUP_NOT_RUN) for i in judged)
    verification = proposal.get("verification") or {}
    unread = (verification.get("merged_prs_searched") == []
              and any(r.get("list") in ("planning", "spare")
                      for r in verification.get("cards") or ()))
    why = None
    if all_failed:
        reason = _one_line(marks[failed_[0]].get("reason"))
        if reason.startswith(groom_lookups.LOOKUP_FAILED):
            reason = reason[len(groom_lookups.LOOKUP_FAILED):]
        why = ALL_LOOKUPS_FAILED + reason
    elif unread:
        gap = ((verification.get("unread") or {}).get("merged_prs") or {})
        why = MERGED_PRS_UNREAD + _one_line(
            gap.get("why") or "no reason was recorded")
    if why and _count_unread(proposal, repo_map):
        why += COUNT_UNREAD_TOO
    return {"lookups_failed": failed_, "all_lookups_failed": all_failed,
            "merged_prs_unread": unread, "not_posted_why": why}


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
        # judgement, in target order.
        "excluded": [{"identifier": i, "reason": marks[i]["reason"]}
                     for i in order if marks[i]["verdict"] == EXCLUDED],
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
        excluded = [f"{r['card']} ({r['excluded']})" for r in rows
                    if r.get("excluded")]
        print(f"groom-verify: {len(rows)} card(s) to verify"
              + (f"; excluded without judgement: {', '.join(excluded)}"
                 if excluded else ""))
        return 0

    if args.command == "prepare":
        started = _now()
        with open(args.started_at_out, "w", encoding="utf-8") as fh:
            fh.write(started + "\n")
        brief = Path(args.brief).read_text(encoding="utf-8")
        text = prepare(_targets_file(args.targets), args.card, brief=brief)
        with open(args.out, "w", encoding="utf-8") as fh:
            fh.write(text)
        if text.strip() == NOT_VERIFIABLE or text.startswith(EXCLUDED_PREFIX):
            print(text.strip())
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
            f"id {before} → {after['id']}"
          + (f"; not posted — {block['not_posted_why']}"
             if block["not_posted_why"] else ""))
    return 0


if __name__ == "__main__":                                  # pragma: no cover
    sys.exit(main())
