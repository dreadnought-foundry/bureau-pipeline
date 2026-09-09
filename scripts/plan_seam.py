#!/usr/bin/env python3
"""The seam reader — an observation-gated second epic, found mechanically
(DRE-3395).

Some plans hide a seam. DRE-3164 was fifteen cards, and three of them —
DRE-3215, DRE-3217, DRE-3218 — could not start until a person had watched
DRE-3166 run live for a week. That is not a later step in one epic; it is a
second epic wearing the first one's number, and everything past the seam is
work the CEO has not green-lit yet because nobody could know what it would
cost until the first half was observed.

A critic can be told to look for that, and reading a plan is the expensive
half of its turn. So the finding is computed BEFORE the critic reads anything,
off the board's own relations, in the words `standards/card-quality.md` fixes:

    DRE-3215, DRE-3217, DRE-3218: wait on observing DRE-3166 live — that is a
    second epic, not a later step.

## The rule, in two sentences

**The waiting set is everything past the seam. The seam exists only when a
build card is in it.** Both this module's tests and the gate card's (DRE-3398)
are written against those two sentences, and if the rule ever moves it moves
here first.

The first sentence is why a downstream OBSERVATION card is listed: a card on
the far side of a seam belongs to the second epic whether it is code or a
switch-on. DRE-3218 is the case in point — an `[OPERATOR]` card transitively
blocked by DRE-3166 through DRE-3215 and DRE-3217, so it is in DRE-3166's
waiting list. The second sentence is why DRE-3218 opens no seam of its own:
nothing but the PROOF/DEMO pair waits on it, so no build work crosses it and
there is no second epic to cut.

## What this module does NOT do

It does not edit `scripts/plan_critic.py`, `scripts/linear_ops.py` or the plan
workflow — sibling cards own all three, and a seam check that widened a board
read would collide with every one of them. It CONSUMES the two reads
`linear_ops.py` already offers (`children-json` for state, `children-detail`
for titles and the formal `blocks` relations), joins them here, and derives
every set it needs from the modules that already define them:
`plan_critic.shipped_work_is_a_finding` for "delivered",
`proof_and_demo.is_proof` / `is_demo` for the pair, and
`proof_and_demo.build_roles()` for what counts as build work.

Wiring this into the plan workflow, and turning the finding into a send-back at
the decision step, is DRE-3398's — it calls the `findings` CLI and adds nothing
to this module.

CLI:
  findings --detail-file F [--structural-file F] [--note-file F]
      `children-json` records on stdin, `children-detail` records in F.
      Prints the finding lines (or `no seam — N card(s)`), writes them one per
      line to the structural file (EMPTY when there are none — to the gate an
      absent file and an empty file are different facts), and appends a seam
      block to an EXISTING note file so the comment `plan_critic.py mechanical`
      already composed carries the seam without that module changing.
      Always exits 0: this is INPUT to a critic, not a gate of its own.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import plan_critic  # noqa: E402
import proof_and_demo  # noqa: E402
import routing_verdict  # noqa: E402

#: The finding, as one sentence with two holes in it. ONE copy in the
#: pipeline: `standards/card-quality.md` writes the same words for humans
#: (DRE-3391), the gate card's test derives what it expects from this module
#: rather than retyping it, and `tests/test_plan_seam.py` pins the text.
FINDING = ("{waiting}: wait on observing {observation} live — that is a "
           "second epic, not a later step.")

#: The heading the seam block opens with when it is appended to the mechanical
#: note. It says what the finding IS, because the same comment carries advisory
#: material and a reader who cannot tell them apart treats both as advice.
NOTE_HEADING = ("Seam (structural — a send-back at the first critic, not "
                "advice):")

#: An OPERATOR card, by title. ANCHORED at the start, the way
#: `proof_and_demo`'s two title patterns are: `Rename the [OPERATOR] section`
#: is an ordinary code card, and a substring read would make it a seam.
_OPERATOR_TITLE = re.compile(r"^\s*\[operator\]", re.IGNORECASE)

#: The label pair that says "a person has to do this and there is no code in
#: it". Either alone is ordinary — `no-code` is on every standards card, and
#: `needs-human` rides on a card parked for a split — so the rule is BOTH.
_OBSERVATION_LABELS = frozenset({"needs-human", "no-code"})

#: A clean-days / clean-releases count, the third way a card says "watch this
#: run before the next thing starts": `after seven clean console days`,
#: `10 clean releases`. The spelled numbers stop at fourteen because past a
#: fortnight nobody writes the word; a longer wait is written as a date.
_CLEAN_COUNT = re.compile(
    r"(\d+|two|three|four|five|six|seven|eight|nine|ten|eleven|twelve|"
    r"thirteen|fourteen)\s+clean\s+\w*\s*(days?|releases?)",
    re.IGNORECASE,
)


# --- The join ---------------------------------------------------------------

def join(cards, details) -> list[dict]:
    """`children-json` and `children-detail` merged on `identifier`.

    Two reads, because they answer different questions and neither is widened
    for this one: `children-json` carries the STATE (so a delivered child can
    be dropped, DRE-3243) and `children-detail` carries the TITLE and the
    formal `blocks` relations (DRE-2746). The result is one record per child —
    `{identifier, title, body, labels, blocked_by, state}` — in
    `children-detail`'s creation order, with any child found only in
    `children-json` appended after it.

    It UNDER-REPORTS and never raises. A child present in only one read gets an
    empty `title`/`blocked_by` or an empty `state`; a record with no identifier
    at all, or an input that is not a list of records, contributes nothing. A
    planning run that crashed on a malformed board read would cost the whole
    plan, and the worst this check can be is silent.
    """
    def records(value):
        return [r for r in value if isinstance(r, dict)] \
            if isinstance(value, list) else []

    by_id = {}
    for rec in records(cards):
        ident = rec.get("identifier")
        if ident:
            by_id[ident] = rec

    out, seen = [], set()
    for rec in records(details):
        ident = rec.get("identifier")
        if not ident or ident in seen:
            continue
        seen.add(ident)
        card = by_id.get(ident) or {}
        out.append({
            "identifier": ident,
            "title": rec.get("title") or "",
            "body": rec.get("body") or card.get("body") or "",
            "labels": list(rec.get("labels") or card.get("labels") or []),
            "blocked_by": [b for b in (rec.get("blocked_by") or []) if b],
            "state": card.get("state") or "",
        })
    for ident, card in by_id.items():
        if ident in seen:
            continue
        out.append({
            "identifier": ident,
            "title": "",
            "body": card.get("body") or "",
            "labels": list(card.get("labels") or []),
            "blocked_by": [],
            "state": card.get("state") or "",
        })
    return out


# --- The three sets ---------------------------------------------------------

def _considered(records) -> list[dict]:
    """The children every definition here is over: the ones that survive the
    delivered-child drop, minus the PROOF/DEMO pair.

    The drop is `plan_critic.shipped_work_is_a_finding`, the same definition
    the collision check uses (DRE-3243) — a delivered card is not something a
    plan is still waiting on, and an observation already made is not a seam to
    build across. The pair is excluded because it is blocked by every other
    child by construction: read as ordinary children, PROOF and DEMO would be
    waiting on every observation card in the epic and every one of them would
    look like a seam.
    """
    return [r for r in (records or [])
            if isinstance(r, dict) and r.get("identifier")
            and plan_critic.shipped_work_is_a_finding(r)
            and not proof_and_demo.is_proof(r.get("title") or "")
            and not proof_and_demo.is_demo(r.get("title") or "")]


def _is_observation(record: dict) -> bool:
    title = record.get("title") or ""
    if _OPERATOR_TITLE.match(title):
        return True
    labels = {str(l).strip().lower() for l in record.get("labels") or []}
    if _OBSERVATION_LABELS <= labels:
        return True
    # The acceptance SECTION, not its checkbox items alone: a wait written as
    # prose under the heading ("after seven clean console days") is still the
    # card's exit condition, and reading only the boxes would miss it.
    section = routing_verdict.acceptance_section(record.get("body") or "") or []
    return any(_CLEAN_COUNT.search(line) for line in section)


def observation_cards(records) -> list[str]:
    """The children somebody has to WATCH before the next thing can start, in
    record order.

    Three signals, any one of which is enough: an `[OPERATOR]` title, the
    `needs-human` + `no-code` label pair, or a clean-days/clean-releases count
    in the acceptance criteria. The pair is never one of them.
    """
    return [r["identifier"] for r in _considered(records) if _is_observation(r)]


def build_cards(records) -> list[str]:
    """The children a BUILD run is dispatched for, in record order.

    Derived from `proof_and_demo.build_roles()` — the roster entries running on
    `agent-task.yml`, today engineer / frontend / devops / database-architect —
    and never restated here, so an `agent:ops` card is not a build card and the
    list moves when `agents.yaml` does.
    """
    roles = {f"agent:{role}" for role in proof_and_demo.build_roles()}
    return [r["identifier"] for r in _considered(records)
            if roles & {str(l).strip().lower() for l in r.get("labels") or []}]


def waiting_on(records, observation: str) -> list[str]:
    """Every child transitively blocked by `observation`, in record order.

    Build cards and later observation cards alike — the waiting set is
    everything past the seam. The walk stays inside the epic's own children: an
    id in a `blocked_by` list that names no child here is ignored, because a
    cross-epic blocker is somebody else's ordering and not this seam's.
    """
    considered = _considered(records)
    known = {r["identifier"] for r in considered}
    if observation not in known:
        return []
    blocks: dict[str, list[str]] = {}
    for rec in considered:
        for blocker in rec["blocked_by"]:
            if blocker in known:
                blocks.setdefault(blocker, []).append(rec["identifier"])
    reached, frontier = set(), [observation]
    while frontier:
        for ident in blocks.get(frontier.pop(), ()):
            if ident not in reached and ident != observation:
                reached.add(ident)
                frontier.append(ident)
    return [r["identifier"] for r in considered if r["identifier"] in reached]


def observation_seams(records) -> list[dict]:
    """`{"observation", "waiting"}` for every observation card with BUILD work
    past it, in record order.

    The build-card condition is the whole rule. A waiting list holding only
    observation cards, or nothing, is not a seam: no build work crosses it, so
    there is no second epic's worth of unscoped code on the far side. It is
    what makes DRE-3166 a seam — DRE-3215 and DRE-3217 build behind it — and
    DRE-3216 and DRE-3218 not.
    """
    builds = set(build_cards(records))
    out = []
    for observation in observation_cards(records):
        waiting = waiting_on(records, observation)
        if builds & set(waiting):
            out.append({"observation": observation, "waiting": waiting})
    return out


def seam_findings(records) -> list[str]:
    """One `FINDING` line per seam, in record order."""
    return [FINDING.format(waiting=", ".join(seam["waiting"]),
                           observation=seam["observation"])
            for seam in observation_seams(records)]


# --- CLI --------------------------------------------------------------------

def _stdin_json(default):
    raw = sys.stdin.read().strip() if not sys.stdin.isatty() else ""
    if not raw:
        return default
    try:
        return json.loads(raw)
    except ValueError:
        return default


def _details(path: str | None) -> list:
    """The `children-detail` records, or an empty list and one line on stderr.

    A missing or unreadable file is read as "no relations known", so the
    reader UNDER-REPORTS rather than crashing a planning run — and it says so
    on stderr, because a check that found nothing and a check that had nothing
    to read are different facts (standards/console-honesty.md rule 2).
    """
    if not path:
        return []
    try:
        with open(path, encoding="utf-8") as fh:
            loaded = json.load(fh)
    except (OSError, ValueError) as exc:
        print(f"plan seam: could not read {path} ({exc}) — reading no "
              "relations, so no seam can be found", file=sys.stderr)
        return []
    return loaded if isinstance(loaded, list) else []


def _write_structural(path: str | None, findings: list[str]) -> None:
    if not path:
        return
    try:
        with open(path, "w", encoding="utf-8") as fh:
            for line in findings:
                fh.write(line + "\n")
    except OSError as exc:
        print(f"plan seam: could not write {path}: {exc}", file=sys.stderr)


def _append_note(path: str | None, findings: list[str]) -> None:
    """The seam block, appended to the note `plan_critic.py mechanical` wrote.

    Only when the file EXISTS: the mechanical note is written with `w`, so a
    file created here would be overwritten by whichever step ran second. And
    only when there is something to say — a note with an empty seam block in
    it teaches a reader to skip the heading.
    """
    if not path or not findings or not os.path.isfile(path):
        return
    try:
        with open(path, "a", encoding="utf-8") as fh:
            fh.write("\n" + NOTE_HEADING + "\n")
            for line in findings:
                fh.write(f"- {line}\n")
    except OSError as exc:
        print(f"plan seam: could not append to {path}: {exc}", file=sys.stderr)


def _cmd_findings(args) -> int:
    records = join(_stdin_json([]), _details(args.detail_file))
    findings = seam_findings(records)
    _write_structural(args.structural_file, findings)
    _append_note(args.note_file, findings)
    if not findings:
        print(f"no seam — {len(records)} card(s)")
    for line in findings:
        print(line)
    # Always 0. The seam is INPUT to the critic's decision step, which is
    # DRE-3398's; a reader that failed the step would take the plan run with it.
    return 0


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    sub = ap.add_subparsers(dest="cmd", required=True)

    f = sub.add_parser("findings",
                       help="seam findings; children-json on stdin")
    f.add_argument("--detail-file", required=True,
                   help="children-detail records, for the blocks relations")
    f.add_argument("--structural-file", default=None,
                   help="the findings, one per line; empty when there are none")
    f.add_argument("--note-file", default=None,
                   help="an existing mechanical note to append the seam to")
    f.set_defaults(fn=_cmd_findings)

    args = ap.parse_args(argv)
    return args.fn(args)


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
