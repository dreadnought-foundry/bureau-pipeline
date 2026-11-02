#!/usr/bin/env python3
"""The one repair every card that arrives in Triage gets (DRE-7178, DRE-8647).

A card the plan critic's bound parks in Triage (`🔒 hold:
reason=plan-critic-bound … by=plan.yml`) had no next step. The hygiene agent's
Triage rule 3 reported it and alarmed after `HYGIENE_TRIAGE_ALARM_HOURS`. On
2026-10-12 DRE-7066 and DRE-7164 sat there about three hours each, and every
finding on both was a defect in the card a rewrite answers. Until DRE-8647 that
park was the only arrival this repair took; on 2026-11-01 none of the five
cards in Triage was one, and the CEO had asked for the lane to be fixed by hand
eight or more times. This module decides the one automatic repair every card
in Triage gets, chosen by why it arrived, and applies the result. It runs no
model: the Triage Repair workflow (`.github/workflows/triage-repair.yml`,
DRE-7179) runs the planner between `claim` and `apply`.

## Which cards (`select`)

The Triage lane is read once, through `board_snapshot.read_board` — the read
the hygiene agent uses. Every card in it is in scope, whether or not it wears
`needs-human`.

For each card the WHOLE thread is read,
`linear_ops.comment_records(card, whole_thread=True)` — never the board read's
fifty-comment window, because one planning attempt after a repair adds about
fifteen comments before the first critic's pass and more after it.

**Why it arrived** is read off what its writer left on it, through
`config/triage-repair.json` (`arrival_row`): one row per unit that
`config/triage-reasons.json` declares, each naming the marker that writer
leaves and the one repair it gets. The card's live stamp (`hold.read_stamp`)
is matched first, then its newest pipeline-authored note opening a row's tag,
then its description; a card matching no row is `rewrite`. The table's
repairs are `rewrite`, `replan`, `relabel`, `none` and `wait`.

The findings come off the newest park note: the newest pipeline-authored
comment opening `🛑`, and every numbered item under `What the critic found:`
(`plan_critic.unfinished_revision_note`) or under
`plan_critic.FINDINGS_SO_FAR_HEADING` (the bound) — every item, not only the
worst of each round as `plan_bound.findings` reads them. A card with no such
list hands the planner one finding instead (`findings_of`): its newest
pipeline-authored park note or hold stamp (`arrival_note`) — a `🛑` note, a
`🔒 hold:` stamp, or a note opening its row's tag — and, for a card created
straight into Triage with neither, its own description. So no card is skipped.
One action per card:

  * `wait` — the row's repair is `wait` (DRE-8849): the card waits on
    something a sweep lifts. The medic's `inherited-red-main` hold waits on
    `main`'s red checks, and the sweep returns the card to In Review once
    they are green, so rewriting it changes nothing. Nothing is claimed and
    nothing is written — not even an `unrepairable` receipt, which would
    page a person over a card that needs none.
  * `spent` — a pipeline-authored comment opening `RECEIPT_KEY` stands
    anywhere on the thread. Once per card, never once per arrival. A
    receipt-shaped comment from any other author grants nothing, by
    `plan_critic.trusted_bodies`' rule, as `plan_bound._is_receipt` reads its own.
  * `none` — the row's repair is `none`: rewriting the card changes nothing
    that holds it (a red check, a spent fix budget, a blocker line naming a
    card the board does not hold, which only its author may reword — nothing
    rewrites a description, DRE-2676). Or the card has children and a
    pipeline-authored `🔁 bound-rewrite:` receipt stands anywhere on the thread
    (`plan_bound.granted`): the planner has had its one extra rewrite, so this
    park is the second (DRE-7181). No planner runs: the claim is followed at
    once by the `unrepairable` receipt naming why.
  * `relabel` — the row's repair is `relabel` (a `no-route` hold): the planner
    names the repo the card's files live in.
  * `escalate` — at least one finding is one `send_back_class.classify`
    places as DECISION, on a card with children too: the bound's own exit
    (`plan_bound.decide`) asks such a finding of the CEO before it ever
    grants a rewrite, and the planner cannot answer it.
  * `replan` — the card has children (or its row's repair is `replan`), no
    such receipt, and findings none of
    which is a decision. An epic's plan is its children, and the planner is
    the agent that rewrites a plan, so the description is never rewritten.
    Its findings are the bound round's (`epic_findings`): an epic's park note
    carries the bound's sentence, and the list is on the critic's note beside
    the round's record. DRE-7072 parked at 2026-10-12 16:25 PT with no
    receipt, and an operator moved it back to Planning at 17:39 PT, where both
    critics passed it.
  * `repair` — every finding is REVISION or uncertain. Uncertain is the
    planner's here, for the reason `plan_bound.UNCERTAIN_BY_STAGE` gives: every
    finding at a bound is the worst of a SEND_BACK round, the critic's own
    "fix the card". All eight of DRE-7066's findings and all seven of
    DRE-7164's read uncertain.

**Order with the hygiene agent.** Some cards are settled mechanically by
`hygiene_triage.py`'s rules (`HYGIENE_RULES`: a moot card, a retired repo, a
proof with an open pull request, a prose blocker, a dependency loop). Those
predicates are imported read-only and run the way the hygiene lane runs them,
in its order, and a card one of them settles is `wait` — left alone until it
has stood in Triage `SETTLE_HOURS` (2) after it arrived. A card still there
then is taken. When it arrived (`arrived_at`) is its newest entry into Triage
on the history read, or its arrival note when that is newer, else its
creation; an arrival no read gives is read as recent. A read a rule makes that
fails skips the card for this pass, said on stderr.

`--out` gets the `repair`, `escalate`, `replan`, `relabel` and `none` cards,
oldest arrival first, at most `--limit`. A card whose `repo:` label names no
repo in `config/repo-map.json` — or that carries none — is handed on with
bureau-pipeline's, where the map lives, so the leg can check one out. Nothing is
written to Linear.

## The claim (`claim`)

`🩹 triage-repair: started`, carrying the card's description as it stood,
fenced, so a person can put it back. A card held `plan-critic-bound` keeps the
words it always had; any other arrival's says the repair started. The claim IS the once-per-card key: a run
that dies after it has spent the card's repair, and hygiene rule 3 alarms on a
`started` receipt once it is `TRIAGE_REPAIR_RUNNING_HOURS` old (DRE-7183).
Exit 3, posting nothing, when a receipt already stands.

## The outcome (`apply`)

`apply` acts only under a standing claim: when the newest pipeline receipt on
the thread is not `started` — no claim, or a finished outcome already stands —
it posts nothing and exits non-zero, so a retried or stale run never repeats a
write or posts a second outcome. The planner runs for up to `REPAIR_MAX_TURNS`
between the claim and the apply, so the card is then read live, before any
write: a card a person or the hygiene agent moved out of Triage, or whose
`needs-human` was cleared under a live stamp, in that window is no longer the
repair's, and the outcome is `unfinished` with nothing written. A card that
wore no hold has none to lift, and its outcome makes no lift. The move to Planning is
conditional on Triage too (`cmd_state(..., expect=(LANE,))`), for a card
moved on while the rewrite's own writes run.

The planner's files are read first, and exactly one outcome is reached:

  * `asked` — `question.md` is non-empty, whichever the action. The
    description is never set, even beside a `revised-card.md`. A question
    `planning_escalation.refusal` refuses is `unfinished` before any write.
    Writes: the lift, then `planning_escalation.escalate(...,
    from_lane="Triage")` — the note, and the move from Triage to Green Light —
    then the receipt, only when `escalate` answered `parked=True`. When it
    stood down it moved nothing, and the receipt is `unfinished`, naming why.
  * `rewritten` — the action is `repair`, there is no question, and
    `revised-card.md` and `repair-answers.md` are both non-empty. Writes: the
    description, `answer_into_card.write` (so a `## Decisions from the CEO`
    block survives the rewrite, DRE-6360), the lift, the move to Planning —
    which fires a plan run, so the critics read the rewritten card — then the
    receipt listing every finding by its number with its answer line.
  * `split` — the action is `repair`, there is no question, and the planner
    found the card is more than one job (DRE-7182). It wrote one of two
    shapes, never both, and `repair-answers.md` beside it:
      - siblings: `revised-card.md` narrowed to the first piece, and each
        other piece as `sibling-<n>.md` — `# <title>`, then a full card body.
        Writes: each sibling in its number order, then the description and
        the Decisions block, so a refused filing leaves the card whole. Each
        sibling is filed through the seam the card's parent decides — under
        an epic being built (`mid_epic.EPIC_ACTIVE_LANES`),
        `mid_epic.discovery` as an addition, so the epic's green light is not
        re-asked; under any other parent, `linear_ops.cmd_subissue`; with no
        parent, `linear_ops.cmd_oneoff` with the card's own `repo:`, `agent:`
        and `initiative:` labels. The seam decides the lane a sibling lands
        in, and nothing here names it. A sibling that fails to file stops the
        sequence: the card stays in Triage, held, with its description
        untouched, and `unfinished` names the cards already filed, which
        stay, and the files not filed.
      - an epic: `reshape.txt` reading exactly `epic`. Writes: the description
        and the Decisions block when a `revised-card.md` stands beside it,
        then the shape, through `planning_shape.stamp` as `by: planner` with
        the model the run used (`--model`, the classifier's stamper rule,
        DRE-3029). A card already stamped another shape refuses it, and the
        refusal is a failed write.
    Then the lift, the move to Planning — where the plan route plans a
    restamped epic as an epic — and the receipt, naming every finding with
    its answer and every card filed. A split is the planner's only in a live
    run: a dry run claims nothing, so `apply` never runs.
  * `replanned` — the action is `replan` (no `--dir`: the workflow claimed
    it and runs no planner). Writes: the bound-rewrite receipt with every
    finding (`plan_bound.post_receipt`, so `plan_bound.py context` puts them
    under `## Findings the critics left open on this card` for the fresh
    attempt, and `plan_bound.granted` reads the card as had), the lift, the
    move to Planning — which fires a plan run, and the route opens a fresh
    planning attempt, so both critics count from zero — then the receipt
    naming the findings. A receipt that fails to post lifts and moves
    nothing. A card whose bound rewrite was granted since `select` read it,
    or whose findings, read again, name a decision, is `unfinished` with
    nothing written.
  * `rewritten`, too — the action is `relabel` and `repo.txt` names exactly
    one key of `config/repo-map.json` (DRE-8647). Writes: the `repo:` label
    (any other `repo:` label off first), the lift, the move to Planning —
    whose exit routes the card afresh — then the receipt. A `repo.txt` naming
    anything else, or none, is `unfinished` with nothing written.
  * `unrepairable` — the action is `none` (no `--dir`: no planner ran). The
    card is decided again on a fresh read of the lane, and the receipt's
    first line is `🩹 triage-repair: unrepairable — <why>`. It is the one
    write: nothing is lifted, moved or rewritten. A card that no longer reads
    `none` is `unfinished`.
  * `unfinished` — anything else, a rewrite or a split written for an
    `escalate` card among it. The card stays in Triage, still held, for the hygiene alarm.

A card whose newest receipt is `unfinished` or `unrepairable` is a card the
repair could not fix — the two words DRE-8649's console alert reads.

Every finished outcome writes in the contract's order: its own writes, then
the lift — before the move, so the plan run the move fires is not refused for
the hold — then the move, then the receipt, LAST. A failed write stops the
sequence: it is said on stderr, `unfinished` names it and every write already
made, and the exit is non-zero. A failure after the lift leaves the card in
Triage unheld, and hygiene rule 3 reads a Triage card whose newest receipt is
`unfinished` whether it is held or not (DRE-7183).

The lift line is `🔓 hold lifted: reason=<code> because=triage-repair
by=triage_repair.py` — `plan-critic-bound`, `epic-rereview-twice` or
`no-route` for the rows that lift, each declared in `config/holds.json` under
`triage_repair_lift`. Its `because` is the act's NAME, never its tag
(`TRIAGE_REPAIR_TAG`): no hold line may carry a tag some reader counts, and
the receipt key is an OPENING, which a lift line (`🔓`) never has.

## CLI

    triage_repair.py select --out <file> [--limit N]
    triage_repair.py claim <CARD>                  exit 3: already repaired
    triage_repair.py apply <CARD> --action repair|escalate|replan|relabel|none
                           [--dir <dir>]
                           [--model <id>]       the model the planner ran on
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from datetime import UTC, datetime, timedelta

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import answer_into_card  # noqa: E402
import board_snapshot  # noqa: E402
import hold  # noqa: E402
import linear_ops  # noqa: E402
import mid_epic  # noqa: E402
import pipeline_act  # noqa: E402
import plan_bound  # noqa: E402
import plan_critic  # noqa: E402
import planning_escalation  # noqa: E402
import planning_shape  # noqa: E402
import prose_blockers  # noqa: E402
import send_back_class  # noqa: E402

_HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(_HERE)

# --------------------------------------------------------------------------- #
# the contract (identical in every card of DRE-7174)                           #
# --------------------------------------------------------------------------- #

LANE = "Triage"
#: Where a rewritten card goes: entering Planning fires a plan run.
PLANNING_LANE = "Planning"
IN_SCOPE_REASON = "plan-critic-bound"

#: The act, and its idempotency key: a pipeline-authored comment whose first
#: line opens `RECEIPT_KEY`, anywhere on the card, is the card's one repair.
ACT = "triage-repair"
RECEIPT_MARK = "🩹"
RECEIPT_KEY = f"{RECEIPT_MARK} {ACT}:"
#: The act's registry tag, which every receipt's trailer carries. Not the act's
#: name: the contract's lift line says `because=triage-repair`, and no hold
#: line may carry a tag some reader counts (`docs/holds.md`), so a `tag in
#: body` count of this act never reads a lift line as one of its receipts.
TRIAGE_REPAIR_TAG = "triage-repair-receipt"

STARTED = "started"
REWRITTEN = "rewritten"
ASKED = "asked"
REPLANNED = "replanned"
SPLIT = "split"
UNFINISHED = "unfinished"
#: No planner ran: rewriting the card changes nothing that holds it (DRE-8647).
UNREPAIRABLE = "unrepairable"
OUTCOMES = (STARTED, REWRITTEN, ASKED, REPLANNED, SPLIT, UNFINISHED, UNREPAIRABLE)

REPAIR = "repair"
ESCALATE = "escalate"
REPLAN = "replan"
RELABEL = "relabel"
NONE = "none"
SPENT = "spent"
#: A card a hygiene Triage rule settles, in its first `SETTLE_HOURS` — or
#: one whose row's repair is `wait`, which a sweep lifts (DRE-8849).
WAIT = "wait"
ACTIONS = (REPAIR, ESCALATE, REPLAN, RELABEL, NONE, SPENT, WAIT)
#: The actions the workflow acts on, and so the ones `--out` carries.
WORKED = (REPAIR, ESCALATE, REPLAN, RELABEL, NONE)
#: The actions `apply` reaches an outcome for.
APPLIED = WORKED

REPAIR_MAX_TURNS = 120
DEFAULT_LIMIT = 3

#: Which writer put a card in Triage, and the one repair that arrival gets.
TABLE_PATH = os.path.join(ROOT, "config", "triage-repair.json")
#: Every Triage writer, which the table must cover (DRE-7525).
REASONS_PATH = os.path.join(ROOT, "config", "triage-reasons.json")
REPO_MAP_PATH = os.path.join(ROOT, "config", "repo-map.json")
#: A table row's repair. `replan`, `relabel`, `none` and `wait` are also
#: select's actions; `rewrite` is select's `repair`, `escalate` or `replan`.
#: `wait` (DRE-8849) is a card that waits on something a sweep lifts — the
#: medic's `inherited-red-main` hold waits on `main`'s red checks — so no
#: planner runs, nothing is claimed and nothing is written. Never `none`:
#: that posts an `unrepairable` receipt, the word the console alerts on.
REWRITE = "rewrite"
REPAIR_KINDS = (REWRITE, REPLAN, RELABEL, NONE, WAIT)

#: The hygiene agent's Triage rules that settle a card mechanically
#: (`hygiene_triage.py`), in its order, and how long they own a card first.
HYGIENE_RULES = ("moot", "retired", "proof_in_review", "prose_blocker", "loop")
SETTLE_HOURS = 2
#: The repo a card whose `repo:` label routes nowhere is read in: the one the
#: repo map lives in, which is what a relabel reads.
HOME_SLUG = "bureau-pipeline"

#: Why a re-parked epic is unrepairable (DRE-7181).
BOUND_REWRITE_SPENT = ("the planner has had its one bound rewrite on this epic — a "
                       f"`{plan_bound.RECEIPT_KEY}` receipt stands on its thread, so "
                       "this park is the second")

#: The lift: `hold.lift(card, LIFT_BECAUSE, LIFT_BY)`.
LIFT_BECAUSE = hold.TRIAGE_REPAIR
LIFT_BY = "triage_repair.py"

#: The planner's output files.
REVISED_CARD = "revised-card.md"
REPAIR_ANSWERS = "repair-answers.md"
QUESTION_FILE = "question.md"
QUESTION_CHOICES = "question-choices.json"
#: The split's (DRE-7182): each piece after the first, and the epic restamp.
SIBLING_FILE = re.compile(r"^sibling-(?P<n>\d+)\.md$")
RESHAPE = "reshape.txt"
#: The relabel's (DRE-8647): one key of `config/repo-map.json`.
REPO_FILE = "repo.txt"
#: The one shape `reshape.txt` may name.
EPIC_SHAPE = "epic"
#: The labels a parentless sibling carries from the card it was split out of.
ONEOFF_LABELS = ("repo:", "agent:", "initiative:")

EXIT_OK = 0
EXIT_UNFINISHED = 1
EXIT_SPENT = 3

PARK_MARK = "🛑"
#: The two headings a park note lists its findings under: the "revision did
#: not finish" note's (`plan_critic.unfinished_revision_note`) and the bound's.
FINDINGS_HEADINGS = ("What the critic found:", plan_critic.FINDINGS_SO_FAR_HEADING)

_ITEM = re.compile(r"^(?P<n>\d+)[.)]\s+(?P<text>\S.*?)\s*$")
_TITLE = re.compile(r"^#\s+(?P<title>\S.*?)\s*$")
#: A finding that says the card is more than one job: the `because` a sibling
#: filed under an epic gives. The first finding when none says so.
_SPLIT_WORDS = re.compile(
    r"\b(?:two|several|separate|more than one)\b.*\b(?:pieces?|jobs?|cards?|parts?)\b"
    r"|\bepic\b|\bsplit\b", re.I)
_RECEIPT = re.compile(
    rf"^{re.escape(RECEIPT_KEY)} (?P<outcome>{'|'.join(OUTCOMES)})(?:$|\s)")

# --- the words ----------------------------------------------------------------

ONCE = "This happens once per card."

# The write names an `unfinished` receipt lists, in the order they are made.
W_BOUND_REWRITE = "post the bound-rewrite receipt"
W_DESCRIPTION = "set the description"
W_DECISIONS = "write the Decisions block"
W_LIFT = "lift the hold"
W_MOVE = f"move the card to {PLANNING_LANE}"
W_ESCALATE = f"escalate the question to {planning_escalation.destination()}"
W_STAMP = f"stamp the card {EPIC_SHAPE}"
W_LABEL = "set the repo: label"
W_RECEIPT = "post the receipt"

# --------------------------------------------------------------------------- #
# reading                                                                      #
# --------------------------------------------------------------------------- #


def _body(entry) -> str:
    return (entry.get("body") if isinstance(entry, dict) else entry) or ""


def _first_line(body: str) -> str:
    text = (body or "").strip()
    return text.splitlines()[0] if text else ""


def _pipeline(entry) -> bool:
    """`plan_critic.trusted_bodies`' rule: a record counts only when the
    pipeline wrote it; a plain string is a body the caller stands behind."""
    return not isinstance(entry, dict) or bool(entry.get("authored_by_pipeline"))


def is_receipt(entry) -> bool:
    """A comment the pipeline wrote whose body opens with `RECEIPT_KEY`. Its
    opening only, so a receipt quoted inside another comment grants nothing."""
    return _pipeline(entry) and _body(entry).lstrip().startswith(RECEIPT_KEY)


def receipts(records) -> list:
    return [entry for entry in records or [] if is_receipt(entry)]


def outcome_of(entry) -> str | None:
    """The receipt's outcome word, or None when its first line names none."""
    match = _RECEIPT.match(_first_line(_body(entry)))
    return match.group("outcome") if match else None


def park_note(records):
    """The newest pipeline-authored comment opening `🛑`, or None."""
    for entry in reversed(list(records or [])):
        if _pipeline(entry) and _body(entry).lstrip().startswith(PARK_MARK):
            return entry
    return None


def park_findings(body: str) -> list[str]:
    """Every numbered item at column zero under the park note's findings
    heading, in its order. [] when the note carries no such list."""
    lines = (body or "").splitlines()
    start = next((i for i, line in enumerate(lines)
                  if line.strip().startswith(FINDINGS_HEADINGS)), None)
    if start is None:
        return []
    found: list[str] = []
    for line in lines[start + 1:]:
        match = _ITEM.match(line)
        if match:
            found.append(match.group("text"))
        elif line.strip() and found:
            break
    return found


def epic_findings(records, card: str) -> list[str]:
    """Every finding an epic's bound left open, in the bound round's order.

    An epic's park note (`🛑 Parked in Triage …`) carries the bound's
    sentence, not its list: the list is on the critic's own note for the round
    that reached the bound, posted beside its record. DRE-7072's park note
    named one of its five. So when the newest record of the CURRENT planning
    attempt (`plan_critic.current_cycle_entries`) is a send-back, that round is
    read with `plan_critic.prior_findings` — the record's reason, then the
    note's list — the reader the next critic's charter uses. Otherwise (a park
    after a round that passed, or a thread with no record on this attempt) the
    park note's own list.
    """
    cycle = plan_critic.current_cycle_entries(list(records or []), card)
    rows = plan_critic.parse_markers(cycle)
    if rows and rows[-1]["result"] == plan_critic.SEND_BACK:
        found = plan_critic.prior_findings(cycle, rows[-1]["stage"])
        if found:
            return found
    note = park_note(records)
    return park_findings(_body(note)) if note is not None else []


def decisions(found: list[str]) -> list[str]:
    return [f for f in found if send_back_class.classify(f) == send_back_class.DECISION]


def split_finding(found: list[str]) -> str:
    """The finding that says the card is more than one job, flattened to the
    one line `mid_epic.discovery` records as an addition's `because`: the
    first that says so, else the first finding."""
    picked = next((f for f in found if _SPLIT_WORDS.search(f)), found[0] if found else "")
    return _flat(picked) or "the planner found this card is more than one job"


def parse_sibling(text: str) -> tuple[str, str] | None:
    """`(title, body)` off a `sibling-<n>.md`: its first line `# <title>`,
    then the card body. None when either is missing."""
    lines = (text or "").strip().splitlines()
    match = _TITLE.match(lines[0]) if lines else None
    body = "\n".join(lines[1:]).strip()
    if match is None or not body:
        return None
    return match.group("title"), body + "\n"


def _labels(card: dict) -> list[str]:
    return [(n or {}).get("name") or "" for n in ((card.get("labels") or {}).get("nodes") or [])]


def _window_bodies(card: dict) -> list[str]:
    return [(n or {}).get("body") or ""
            for n in linear_ops.window_nodes(card.get("comments"))]


def in_scope(card: dict) -> bool:
    """In Triage. Every card there is the repair's, held or not (DRE-8647)."""
    return ((card.get("state") or {}).get("name") or "") == LANE


def _load(path: str) -> dict:
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)


def load_table(path: str | None = None) -> dict:
    return _load(path or TABLE_PATH)


def unhandled_units(reasons: dict | None = None, table: dict | None = None) -> list[str]:
    """Every unit `config/triage-reasons.json` declares with no row in the
    table, then every row naming a unit it does not declare, in file order.
    [] when the two agree, so a Triage writer added tomorrow cannot arrive
    unhandled."""
    reasons = reasons if reasons is not None else _load(REASONS_PATH)
    table = table if table is not None else load_table()
    declared = [r.get("unit") for r in reasons.get("reasons") or []] + [
        r.get("unit") for r in reasons.get("unreasoned") or []]
    rows = [r.get("unit") for r in table.get("rows") or []]
    return [u for u in declared if u not in rows] + [u for u in rows if u not in declared]


def _repo_map() -> dict:
    return {k: v for k, v in _load(REPO_MAP_PATH).items() if not k.startswith("_")}


def _opens_tag(first_line: str, tag: str) -> bool:
    """A note's first line opening `<mark> <tag>:` — `🧹 planning-stall-park:`."""
    return bool(re.match(rf"^\S+ {re.escape(tag)}:", first_line or ""))


def arrival_row(card: dict, records, table: dict | None = None) -> dict | None:
    """The table row of the writer that put this card in Triage, read off
    what it left: the card's live stamp, then its newest pipeline-authored
    note opening a row's tag, then its description. None for a card matching
    no row, which is `rewrite`."""
    rows = (table if table is not None else load_table()).get("rows") or []
    stamp = hold.read_stamp([_body(r) for r in records or []])
    if stamp:
        for row in rows:
            if (row.get("marker") or {}).get("hold") == stamp["reason"]:
                return row
    for entry in reversed(list(records or [])):
        if not _pipeline(entry):
            continue
        first = _first_line(_body(entry))
        for row in rows:
            tag = (row.get("marker") or {}).get("tag")
            if tag and _opens_tag(first, tag):
                return row
    description = card.get("description") or ""
    for row in rows:
        phrase = (row.get("marker") or {}).get("description")
        if phrase and phrase in description:
            return row
    return None


def _arrival_entries(records, row: dict | None = None) -> tuple:
    """The newest pipeline-authored park note — a comment opening `🛑`, or one
    opening its row's tag — and the newest pipeline-authored `🔒 hold:` stamp,
    each None when the thread carries none."""
    tag = ((row or {}).get("marker") or {}).get("tag")
    note = stamp = None
    for entry in reversed(list(records or [])):
        if not _pipeline(entry):
            continue
        first = _first_line(_body(entry))
        if note is None and (first.startswith(PARK_MARK) or (tag and _opens_tag(first, tag))):
            note = entry
        if stamp is None and first.startswith(hold.STAMP_PREFIX):
            stamp = entry
    return note, stamp


def arrival_note(records, row: dict | None = None):
    """The card's newest pipeline-authored park note, else its newest hold
    stamp. None when the card carries neither — a card created straight into
    Triage."""
    note, stamp = _arrival_entries(records, row)
    return note if note is not None else stamp


def _is_epic(card: dict) -> bool:
    return bool((card.get("children") or {}).get("nodes"))


def findings_of(card: dict, records, row: dict | None = None, describe=None) -> list[str]:
    """What the planner answers: the park note's numbered findings (an epic's
    bound round's, `epic_findings`), else one finding — the newest park note
    or hold stamp, flattened, else the card's own description. Never [], so
    no card is skipped (DRE-8647). `describe` reads the description when the
    card in hand does not carry it."""
    if _is_epic(card):
        found = epic_findings(records, card.get("identifier") or "")
    else:
        note = park_note(records)
        found = park_findings(_body(note)) if note is not None else []
    if found:
        return found
    note = arrival_note(records, row)
    if note is not None:
        return [_flat(_body(note))]
    description = card.get("description")
    if description is None and describe is not None:
        description = describe()
    return [_flat(description or "")
            or "the card carries no park note, no hold stamp and no description"]


def _when(value) -> datetime | None:
    try:
        when = datetime.fromisoformat(str(value or "").replace("Z", "+00:00"))
    except ValueError:
        return None
    return when if when.tzinfo else when.replace(tzinfo=UTC)


def arrived_at(card: dict, records, row: dict | None = None) -> datetime | None:
    """When the card arrived in Triage: its newest entry on the history read,
    or its newest park note or hold stamp when that is newer — the note lands
    with the move — else the card's creation. None when no read gives one."""
    entered = [_when((n or {}).get("createdAt"))
               for n in ((card.get("history") or {}).get("nodes") or [])
               if (((n or {}).get("toState") or {}).get("name") or "") == LANE]
    posted = [_when(e.get("created_at")) for e in _arrival_entries(records, row)
              if isinstance(e, dict)]
    seen = [w for w in [*entered, *posted] if w is not None]
    return max(seen) if seen else _when(card.get("createdAt"))


def _repo(card: dict) -> str:
    """The card's `repo:` label as `owner/name`, through `config/repo-map.json`.
    A card whose label names no repo the map holds — or that carries none —
    is read in bureau-pipeline's, where the map lives (DRE-8647)."""
    repos = _repo_map()
    for name in _labels(card):
        if name.lower().startswith("repo:"):
            slug = name[len("repo:"):].strip().lower()
            if slug in repos:
                return repos[slug]
    return repos.get(HOME_SLUG) or ""


def _claimed_ids(card: dict, records) -> list[str]:
    """The ids a prose blocker claims with no relation behind them, off the
    card's description and relations; else the ids the sweep's newest
    refusal named."""
    ids = sorted(prose_blockers.undeclared_claims(card))
    if ids:
        return ids
    for entry in reversed(list(records or [])):
        first = _first_line(_body(entry))
        if _pipeline(entry) and _opens_tag(first, prose_blockers.CARD_TAG):
            named = re.search(r"dependency on (?P<ids>.+?) in its", first)
            return re.findall(r"\b[A-Z]+-\d+\b", named.group("ids")) if named else []
    return []


def _unrepairable_why(row: dict, card: dict, records) -> str:
    why = row.get("why") or "rewriting the card changes nothing that holds it"
    if "{ids}" in why:
        why = why.replace("{ids}", ", ".join(_claimed_ids(card, records))
                          or "a card it names")
    return why


def decide(card: dict, records, table: dict | None = None) -> dict:
    """The one action for one card in Triage, off its whole thread. Pure."""
    row = arrival_row(card, records, table)
    kind = (row or {}).get("repair") or REWRITE
    epic = _is_epic(card)
    found = findings_of(card, records, row)
    picked = decisions(found) if kind in (REWRITE, REPLAN) else []
    spent = receipts(records)
    if kind == WAIT:
        action, why = WAIT, row.get("why") or "it waits on something a sweep lifts"
    elif spent:
        word = outcome_of(spent[-1]) or "unreadable"
        action, why = SPENT, (f"the card has had its one automatic repair — a "
                              f"`{word}` receipt stands on its thread")
    elif kind == NONE:
        action, why = NONE, _unrepairable_why(row, card, records)
    elif kind == RELABEL:
        action, why = RELABEL, ("it is held because no repo's run can build it — the "
                                "planner names the repo its files live in")
    elif epic and plan_bound.granted(records):
        action, why = NONE, BOUND_REWRITE_SPENT
    elif picked:
        action, why = ESCALATE, (f"{len(picked)} of {len(found)} finding(s) name a "
                                 "choice only the CEO can make")
    elif epic or kind == REPLAN:
        action, why = REPLAN, (f"an epic's plan is its children — it goes back "
                               f"to {PLANNING_LANE} once, with all "
                               f"{len(found)} finding(s) in front of the planner")
    else:
        action, why = REPAIR, (f"all {len(found)} finding(s) are the planner's to "
                               "answer in a rewrite")
    return {
        "card": card.get("identifier") or "",
        "title": card.get("title") or "",
        "repo": _repo(card),
        "action": action,
        "why": why,
        "findings": found,
        "decisions": picked,
        "max_turns": REPAIR_MAX_TURNS,
        "_arrived": arrived_at(card, records, row),
    }


def _now() -> datetime:
    return datetime.now(UTC)


class _HygieneRules:
    """The hygiene agent's Triage rules that settle a card, run read-only the
    way its lane runs them (`hygiene_triage._plan_card`): a held `no-route`
    card and a card held on a planning reason are that lane's rows, never
    settled, so a rule after them never reads one. Built once a pass, on the
    first card that needs it."""

    def __init__(self, cards: list[dict], now: datetime):
        self.cards, self.now = cards, now
        self.ctx = self.seen = None

    def settles(self, card: dict) -> str | None:
        """The name of the rule that settles `card`, or None."""
        # Imported here: hygiene_triage imports this module.
        import hygiene
        import hygiene_triage
        if self.seen is None:
            self.ctx = hygiene.make_context(hygiene.HOME_OWNER, now=self.now)
            self.seen = hygiene_triage._Pass(
                hygiene.Board(lanes={LANE: list(self.cards)}), self.ctx)
        ctx, seen = self.ctx, self.seen
        if hygiene_triage.moot(card, ctx) is not None:
            return "moot"
        stamp = hold.read_stamp(hygiene_triage._bodies(card))
        if hygiene_triage.held_no_route(card) is not None or (
                hygiene_triage.NEEDS_HUMAN in hygiene_triage._labels(card)
                and stamp is not None
                and stamp["reason"] in hygiene_triage.PLANNING_REASONS):
            return None
        rules = {"retired": lambda: hygiene_triage.retired(card, ctx, seen),
                 "proof_in_review": lambda: hygiene_triage.proof_in_review(card, ctx),
                 "prose_blocker": lambda: hygiene_triage.prose_blocker(card, ctx, seen),
                 "loop": lambda: hygiene_triage.loop(card, ctx, seen)}
        for name in HYGIENE_RULES[1:]:
            if rules[name]() is not None:
                return name
        return None


def select(limit: int = DEFAULT_LIMIT) -> list[dict]:
    """Every card in Triage, decided, and the worked ones, oldest arrival
    first, at most `limit`. Prints one line per card."""
    cards, _requests = board_snapshot.read_board((LANE,))
    table = load_table()
    now = _now()
    hygiene_rules = _HygieneRules(cards, now)
    rows: list[dict] = []
    for card in cards:
        if not in_scope(card):
            continue
        ident = card.get("identifier") or ""
        try:
            records = linear_ops.comment_records(ident, whole_thread=True)
        except Exception as exc:  # noqa: BLE001 — one unread card, never the pass
            print(f"triage-repair: {ident} — could not read its thread ({exc}); "
                  "skipped this pass", file=sys.stderr)
            continue
        row = decide(card, records, table)
        arrived = row["_arrived"]
        if row["action"] in WORKED and (
                arrived is None or now - arrived < timedelta(hours=SETTLE_HOURS)):
            try:
                rule = hygiene_rules.settles(card)
            except Exception as exc:  # noqa: BLE001 — one unread card, never the pass
                print(f"triage-repair: {ident} — a hygiene rule's read failed ({exc}); "
                      "skipped this pass", file=sys.stderr)
                continue
            if rule is not None:
                since = "at a time no read gives" if arrived is None else (
                    arrived.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"))
                row["action"], row["why"] = WAIT, (
                    f"hygiene's `{rule}` rule settles it first — this repair takes "
                    f"it only if it is still in {LANE} {SETTLE_HOURS} hours after "
                    f"it arrived ({since})")
        print(f"triage-repair: {ident} → {row['action']} — {row['why']}")
        if row["action"] in WORKED:
            rows.append(row)
    rows.sort(key=lambda r: r["_arrived"] or _EARLIEST)
    return [{k: v for k, v in r.items() if not k.startswith("_")}
            for r in rows[:max(0, limit)]]


_EARLIEST = datetime.min.replace(tzinfo=UTC)


# --------------------------------------------------------------------------- #
# the receipts                                                                 #
# --------------------------------------------------------------------------- #


def _flat(text: str) -> str:
    return " ".join(str(text).split())


def _fence(text: str) -> str:
    """A backtick fence longer than any backtick run inside `text`."""
    longest = max((len(run) for run in re.findall(r"`+", text or "")), default=0)
    return "`" * max(3, longest + 1)


def started_body(description: str, bound: bool = True) -> str:
    """The claim. `bound` — the card is held `plan-critic-bound` — keeps the
    words DRE-7178's claim always had; any other arrival's says the repair
    its arrival calls for has started (DRE-8647)."""
    fence = _fence(description)
    opening = (f"{RECEIPT_KEY} {STARTED} — this card was parked in {LANE} by the plan "
               "critic's bound, and the planner is now answering every finding of its "
               "park note once, without a person." if bound else
               f"{RECEIPT_KEY} {STARTED} — this card is in {LANE}, and the one "
               "automatic repair its arrival calls for has started, without a person.")
    return "\n\n".join([
        f"{opening} {ONCE} The description as it stood "
        "before the repair, so a person can put it back:",
        f"{fence}\n{description or ''}\n{fence}",
    ])


def _answer_lines(text: str) -> dict:
    out: dict = {}
    for line in (text or "").splitlines():
        match = _ITEM.match(line.strip())
        if match:
            out.setdefault(int(match.group("n")), match.group("text"))
    return out


def _answered(found: list[str], answers_text: str) -> str:
    """Every finding by its number, each with its answer line."""
    answers = _answer_lines(answers_text)
    listed = "\n".join(
        f"{n}. {_flat(f)}\n   Answer: "
        f"{_flat(answers.get(n) or 'the planner wrote no answer line for this finding')}"
        for n, f in enumerate(found, 1))
    return listed or "(the park note carried no findings)"


def rewritten_body(found: list[str], answers_text: str, held: bool = True) -> str:
    lifted = "the hold is lifted, and the card" if held else "and the card"
    return "\n\n".join([
        f"{RECEIPT_KEY} {REWRITTEN} — the planner rewrote this card once to "
        f"answer every finding of its park note, {lifted} is back in "
        f"{PLANNING_LANE}, where a fresh plan run has the "
        f"critics read the rewritten card. {ONCE} The findings, each with its "
        "answer:",
        _answered(found, answers_text),
    ])


def relabeled_body(slug: str, held: bool = True) -> str:
    """A relabel's receipt (DRE-8647): `rewritten`, naming the repo."""
    lifted = "its hold is lifted, and it" if held else "and it"
    return (f"{RECEIPT_KEY} {REWRITTEN} — the planner named `{slug}` as the repo "
            f"this card's files live in, so it now carries `repo:{slug}`, {lifted} "
            f"is back in {PLANNING_LANE}, whose exit routes it afresh. {ONCE}")


def unrepairable_body(why: str) -> str:
    """The `none` outcome (DRE-8647): its first line is the outcome and why."""
    return (f"{RECEIPT_KEY} {UNREPAIRABLE} — {_flat(why)}\n\n"
            "No planner ran: the description is unchanged, nothing is lifted and "
            f"nothing is moved. {ONCE} A person reads it from here.")


def split_body(found: list[str], answers_text: str, filed: list[tuple[str, str]],
               reshaped: bool) -> str:
    """The `split` receipt: every card filed, and every finding with its
    answer. `filed` is `(identifier, title)` per sibling, in filing order."""
    if reshaped:
        how = (f"its pieces are one feature whose plan the CEO approves, so it "
               f"is stamped `{EPIC_SHAPE}`, the hold is lifted, and the card is "
               f"back in {PLANNING_LANE}, where the plan route plans it as an "
               f"{EPIC_SHAPE}")
    else:
        how = (f"it is narrowed to its first piece and {len(filed)} card(s) "
               f"are filed beside it for the rest, the hold is lifted, and the "
               f"card is back in {PLANNING_LANE}, where a fresh plan run has "
               "the critics read the narrowed card")
    parts = [f"{RECEIPT_KEY} {SPLIT} — the planner found this card is more "
             f"than one job and split it once: {how}. {ONCE}"]
    if filed:
        parts.append("The cards filed:\n" + "\n".join(
            f"- {ident} — {_flat(title)}" for ident, title in filed))
    parts += ["The findings, each with its answer:", _answered(found, answers_text)]
    return "\n\n".join(parts)


def replanned_body(found: list[str]) -> str:
    listed = "\n".join(f"{n}. {_flat(f)}" for n, f in enumerate(found, 1))
    return "\n\n".join([
        f"{RECEIPT_KEY} {REPLANNED} — this epic was parked at the plan "
        "critic's bound before the planner had its one bound rewrite. The "
        "rewrite's receipt above carries every finding the bound left open, "
        f"the hold is lifted, and the card is back in {PLANNING_LANE}, where "
        "a fresh planning attempt starts and both critics count from zero. "
        f"{ONCE} The findings in front of the planner:",
        listed or "(the park carried no findings)",
    ])


def asked_body(set_aside: bool, split_aside: bool = False) -> str:
    parts = [
        f"{RECEIPT_KEY} {ASKED} — the planner found a choice on this card only "
        "the CEO can make, so it is asked as one question in "
        f"{planning_escalation.destination()} rather than guessed, and the "
        f"card's description is unchanged. {ONCE}",
    ]
    if set_aside:
        parts.append("The planner's rewrite was set aside: with a question "
                     "open, the description is not set.")
    if split_aside:
        parts.append("The planner's split was set aside: with a question open, "
                     "no card is filed and no shape is stamped.")
    return "\n\n".join(parts)


def unfinished_body(what: str, made: list[str]) -> str:
    done = ", ".join(made) if made else "none"
    return (f"{RECEIPT_KEY} {UNFINISHED} — {what}. Writes already made: {done}. "
            f"The card has had its one automatic repair; a person reads it from "
            "here.")


def _post(card: str, body: str) -> None:
    """Post one receipt through the act registry. Raises on any failure,
    including a comment cap, so a caller never reads a receipt as posted that
    did not land."""
    problem = linear_ops.cmd_comment(card, pipeline_act.receipt("triage-repair", body))
    if problem:
        raise RuntimeError(problem)


# --------------------------------------------------------------------------- #
# claim                                                                        #
# --------------------------------------------------------------------------- #


def claim(card: str) -> int:
    records = linear_ops.comment_records(card, whole_thread=True)
    if receipts(records):
        print(f"triage-repair: {card} — a receipt already stands; its one "
              f"repair is spent. Exit {EXIT_SPENT}, nothing posted")
        return EXIT_SPENT
    stamp = hold.read_stamp([_body(r) for r in records])
    description = answer_into_card._read_description(card)
    _post(card, started_body(description,
                             bool(stamp) and stamp["reason"] == IN_SCOPE_REASON))
    print(f"triage-repair: {card} → {STARTED}")
    return EXIT_OK


# --------------------------------------------------------------------------- #
# apply                                                                        #
# --------------------------------------------------------------------------- #


def _read(directory: str | None, name: str) -> str:
    if not directory:
        return ""
    try:
        with open(os.path.join(directory, name), encoding="utf-8") as fh:
            return fh.read()
    except OSError:
        return ""


class _Stopped(Exception):
    """A write failed; `apply` posts `unfinished` and exits non-zero."""

    def __init__(self, what: str):
        super().__init__(what)
        self.what = what


class _Writes:
    """The sequence of one outcome's writes: each runs, or the sequence stops."""

    def __init__(self, card: str):
        self.card = card
        self.made: list[str] = []

    def do(self, name: str, fn, named=None) -> object:
        """Run one write. `named`, given the write's result, is how it is
        listed as made — a filed sibling is listed by the card it became."""
        try:
            result = fn()
        except Exception as exc:  # noqa: BLE001 — every failure is named, then stops
            raise _Stopped(f"the write to {name} failed ({exc})") from exc
        self.made.append(named(result) if named else name)
        return result


def _decisions_block(card: str) -> None:
    did, _ = answer_into_card.write(card)
    if did.startswith("could not write") or did.startswith("could not check: could not read"):
        raise RuntimeError(did)


def _bound_rewrite(card: str, found: list[str]) -> None:
    """The bound-rewrite receipt, through `plan_bound.post_receipt`: its text
    and its act stay emitted from `plan_bound.py`."""
    problem = plan_bound.post_receipt(card, found)
    if problem:
        raise RuntimeError(problem)


def _move_to_planning(card: str) -> None:
    """Conditional on Triage: a card moved on while the writes before this
    one ran is refused, never dragged back into planning."""
    if not linear_ops.cmd_state(card, PLANNING_LANE, expect=(LANE,)):
        raise RuntimeError(f"a guard refused the move to {PLANNING_LANE}")


def _sibling_files(directory: str | None) -> list[tuple[str, str]]:
    """Every `sibling-<n>.md` the planner wrote, `(name, text)`, in number
    order — `sibling-10.md` after `sibling-2.md`."""
    if not directory:
        return []
    try:
        names = os.listdir(directory)
    except OSError:
        return []
    found = sorted((int(m.group("n")), name) for name in names
                   if (m := SIBLING_FILE.match(name)))
    return [(name, _read(directory, name)) for _n, name in found]


_LINEAGE_QUERY = """query($id: String!) { issue(id: $id) {
     labels { nodes { name } }
     parent { identifier state { name } }
   } }"""


def _lineage(card: str) -> dict:
    """The card's labels, and its parent with the parent's lane — the facts
    that decide the seam a sibling is filed through. One read."""
    issue = (linear_ops.gql(_LINEAGE_QUERY, {"id": card}) or {}).get("issue") or {}
    parent = issue.get("parent") or {}
    return {"labels": _labels(issue),
            "parent": parent.get("identifier") or "",
            "parent_lane": (parent.get("state") or {}).get("name") or ""}


def _file_sibling(lineage: dict, title: str, body: str, because: str) -> str:
    """File one sibling through the seam the card's parent decides, and return
    the card it became. The seam decides the lane it lands in."""
    parent = lineage["parent"]
    if parent and lineage["parent_lane"] in mid_epic.EPIC_ACTIVE_LANES:
        ident = mid_epic.discovery(linear_ops, parent, kind=mid_epic.ADDITION,
                                   because=because, title=title, body=body)
    elif parent:
        ident = linear_ops.cmd_subissue(parent, title, body)["identifier"]
    else:
        # A literal guard on the label itself, so `label_writers.py check`
        # reads every label handed on as one of these prefixes (DRE-6362).
        flags: list[str] = []
        for label in lineage["labels"]:
            if label.startswith(ONEOFF_LABELS):
                flags += ["--label", label]
        ident = linear_ops.cmd_oneoff(title, body, *flags)["identifier"]
    if not ident:
        raise RuntimeError("the seam answered no card")
    return ident


def _stamp_epic(card: str, finding: str, model: str) -> None:
    """`epic`, through the one seam both stampers write by, as the planner
    with the model it ran on. A refusal — the card already carries a shape —
    is a failed write."""
    refusal = planning_shape.stamp(
        linear_ops, card, EPIC_SHAPE,
        f"the Triage repair found this card is more than one job ({finding}), "
        "and the planner read its pieces as one feature whose plan the CEO "
        "approves",
        by=planning_shape.BY_PLANNER, model=model)
    if refusal is not None:
        raise RuntimeError(refusal)


def _claim_stands(records) -> bool:
    """The newest pipeline receipt on the thread is this card's `started`."""
    spent = receipts(records)
    return bool(spent) and outcome_of(spent[-1]) == STARTED


def _moved_on(issue: dict, records) -> str | None:
    """Why the card is no longer the repair's, off its live read, or None
    while it still stands in Triage — wearing the hold when its thread holds
    a live stamp, so a hold someone else lifted is seen."""
    lane = ((issue.get("state") or {}).get("name") or "")
    if lane != LANE:
        return f"the card is no longer in {LANE} — it is in {lane or 'no lane'}"
    stamp = hold.read_stamp([_body(r) for r in records])
    if stamp is not None and not _wears_hold(issue):
        return (f"the card no longer wears `{hold.HOLD_LABEL}` — its hold was "
                "lifted by someone else")
    return None


def _wears_hold(issue: dict) -> bool:
    return hold.HOLD_LABEL in [n.lower() for n in _labels(issue)]


def _lift(writes: _Writes, card: str, reason: str, held: bool) -> None:
    """The lift, when the card wears the hold: a card that wore none has
    nothing to lift (DRE-8647)."""
    if held:
        writes.do(W_LIFT, lambda: hold.lift(card, LIFT_BECAUSE, LIFT_BY, reason=reason))


def _relabel(card: str, slug: str, others: list[str]) -> None:
    """The card's `repo:` label set to `slug`: every other `repo:` label off,
    then this one on."""
    for name in others:
        linear_ops.remove_label(card, name)
    linear_ops.add_label(card, f"repo:{slug}")


def _finish(card: str, writes: _Writes, what: str) -> int:
    """Post `unfinished` naming `what` and the writes made, and exit non-zero."""
    print(f"ERROR: {card}: {what}. Writes already made: "
          f"{', '.join(writes.made) or 'none'}", file=sys.stderr)
    try:
        _post(card, unfinished_body(what, writes.made))
    except Exception as exc:  # noqa: BLE001 — the newest receipt stays `started`
        print(f"ERROR: {card}: the unfinished receipt could not be posted ({exc}); "
              "the newest receipt is still the claim", file=sys.stderr)
    print(f"triage-repair: {card} → {UNFINISHED}")
    return EXIT_UNFINISHED


def _unrepairable(card: str, writes: _Writes, records) -> int:
    """The `none` outcome: the card decided again on a fresh read of the
    lane, its claim set aside, then the one receipt."""
    try:
        cards, _requests = board_snapshot.read_board((LANE,))
    except Exception as exc:  # noqa: BLE001
        return _finish(card, writes, f"the {LANE} lane could not be read ({exc})")
    found = next((c for c in cards if c.get("identifier") == card), None)
    if found is None:
        return _finish(card, writes, f"the card is no longer on the {LANE} lane's read")
    row = decide(found, [r for r in records if not is_receipt(r)])
    if row["action"] != NONE:
        return _finish(card, writes, f"the card no longer reads as unrepairable — it "
                       f"reads {row['action']}: {row['why']}")
    writes.do(W_RECEIPT, lambda: _post(card, unrepairable_body(row["why"])))
    print(f"triage-repair: {card} → {UNREPAIRABLE}")
    return EXIT_OK


def apply(card: str, action: str, directory: str | None,
          model: str | None = None) -> int:
    question = _read(directory, QUESTION_FILE)
    revised = _read(directory, REVISED_CARD)
    answers = _read(directory, REPAIR_ANSWERS)
    siblings = _sibling_files(directory)
    reshape = _read(directory, RESHAPE)
    repo_text = _read(directory, REPO_FILE)
    writes = _Writes(card)
    try:
        records = linear_ops.comment_records(card, whole_thread=True)
    except Exception as exc:  # noqa: BLE001
        return _finish(card, writes, f"the card's thread could not be read ({exc})")
    if not _claim_stands(records):
        print(f"ERROR: {card}: no claim stands — the newest receipt is not "
              f"`{STARTED}`, so this card's one repair is not this run's to "
              "apply. Nothing posted", file=sys.stderr)
        return EXIT_UNFINISHED
    try:
        issue = linear_ops.get_issue(card, fresh=True)
    except Exception as exc:  # noqa: BLE001
        return _finish(card, writes, f"the card could not be read ({exc})")
    gone = _moved_on(issue, records)
    if gone is not None:
        return _finish(card, writes, gone)
    held = _wears_hold(issue)
    stamp = hold.read_stamp([_body(r) for r in records])
    reason = stamp["reason"] if stamp else "manual"
    # The card as the findings read it; its description only when no park
    # note or stamp stands in for it.
    live = {"identifier": card, "children": issue.get("children") or {}}
    row = arrival_row(live, records)

    def found() -> list[str]:
        return findings_of(live, records, row,
                           describe=lambda: answer_into_card._read_description(card))

    try:
        if action == NONE:
            return _unrepairable(card, writes, records)

        if question.strip():
            refused = planning_escalation.refusal(question)
            if refused is not None:
                return _finish(card, writes, "the planner's question was refused "
                               f"before it reached the CEO — {refused}")
            choices = planning_escalation.read_choices(
                os.path.join(directory, QUESTION_CHOICES) if directory else None)
            _lift(writes, card, reason, held)
            outcome = writes.do(W_ESCALATE, lambda: planning_escalation.escalate(
                linear_ops, card, question, choices=choices, from_lane=LANE))
            if not outcome.parked:
                return _finish(card, writes, "the escalation stood down and moved "
                               f"nothing — {outcome.stood_down}")
            writes.do(W_RECEIPT, lambda: _post(
                card, asked_body(bool(revised.strip()),
                                 bool(siblings or reshape.strip()))))
            print(f"triage-repair: {card} → {ASKED}")
            return EXIT_OK

        if action == RELABEL:
            slug = repo_text.strip()
            if not slug:
                return _finish(card, writes, f"the planner wrote no {REPO_FILE}")
            if slug not in _repo_map():
                return _finish(card, writes, f"{REPO_FILE} names {_flat(slug)[:60]!r}, "
                               "which is not a key of config/repo-map.json")
            others = [n for n in _labels(issue)
                      if n.lower().startswith("repo:") and n != f"repo:{slug}"]
            writes.do(W_LABEL, lambda: _relabel(card, slug, others))
            _lift(writes, card, reason, held)
            writes.do(W_MOVE, lambda: _move_to_planning(card))
            writes.do(W_RECEIPT, lambda: _post(card, relabeled_body(slug, held)))
            print(f"triage-repair: {card} → {REWRITTEN}")
            return EXIT_OK

        if action == REPLAN:
            if plan_bound.granted(records):
                return _finish(card, writes, "the planner has already had its "
                               "one bound rewrite on this card, so it is not "
                               "re-planned twice")
            listed = found()
            picked = decisions(listed)
            if picked:
                return _finish(card, writes, f"{len(picked)} of {len(listed)} "
                               "finding(s) name a choice only the CEO can make, "
                               "so the epic is asked, not re-planned")
            writes.do(W_BOUND_REWRITE, lambda: _bound_rewrite(card, listed))
            _lift(writes, card, reason, held)
            writes.do(W_MOVE, lambda: _move_to_planning(card))
            writes.do(W_RECEIPT, lambda: _post(card, replanned_body(listed)))
            print(f"triage-repair: {card} → {REPLANNED}")
            return EXIT_OK

        missing = [name for name, text in ((REVISED_CARD, revised),
                                           (REPAIR_ANSWERS, answers))
                   if not text.strip()]
        if action != REPAIR:
            return _finish(card, writes, f"the action is {action} and the planner "
                           f"wrote no {QUESTION_FILE}, so there is nothing to ask"
                           + (" — a rewrite is not this action's outcome"
                              if revised.strip() else "")
                           + (" — a split is not this action's outcome"
                              if siblings or reshape.strip() else ""))
        if siblings or reshape.strip():
            return _split(card, writes, found(), reason, held, revised=revised,
                          answers=answers, siblings=siblings, reshape=reshape,
                          model=model)
        if missing:
            return _finish(card, writes, "the planner wrote no "
                           + " and no ".join(missing))
        listed = found()
        writes.do(W_DESCRIPTION, lambda: linear_ops.set_description(card, revised))
        writes.do(W_DECISIONS, lambda: _decisions_block(card))
        _lift(writes, card, reason, held)
        writes.do(W_MOVE, lambda: _move_to_planning(card))
        writes.do(W_RECEIPT, lambda: _post(card, rewritten_body(listed, answers, held)))
        print(f"triage-repair: {card} → {REWRITTEN}")
        return EXIT_OK
    except _Stopped as stop:
        return _finish(card, writes, stop.what)


def _split(card: str, writes: _Writes, found: list[str], reason: str, held: bool, *,
           revised: str, answers: str, siblings: list[tuple[str, str]], reshape: str,
           model: str | None) -> int:
    """The `split` outcome (DRE-7182): every file checked before any write,
    then the outcome's own writes, the lift, the move and the receipt. A
    `_Stopped` raised here is `apply`'s to finish."""
    shape = reshape.strip()
    if siblings and shape:
        return _finish(card, writes, "the planner wrote both "
                       + ", ".join(name for name, _ in siblings)
                       + f" and {RESHAPE} — a split is siblings or an "
                       f"{EPIC_SHAPE}, never both")
    if shape and shape != EPIC_SHAPE:
        return _finish(card, writes, f"{RESHAPE} reads {_flat(shape)[:60]!r}, and "
                       f"the one shape it may name is `{EPIC_SHAPE}`")
    # Siblings need the narrowed card; an epic may keep its description.
    missing = [REVISED_CARD] if siblings and not revised.strip() else []
    if not answers.strip():
        missing.append(REPAIR_ANSWERS)
    if missing:
        return _finish(card, writes, "the planner split the card and wrote no "
                       + " and no ".join(missing))
    if shape and not (model or "").strip():
        return _finish(card, writes, f"no --model was passed, and an {EPIC_SHAPE} "
                       "stamp by the planner must name the model it ran on")
    pieces: list[tuple[str, str, str]] = []
    for name, text in siblings:
        piece = parse_sibling(text)
        if piece is None:
            return _finish(card, writes, f"{name} does not open with a `# <title>` "
                           "line followed by a card body, so no sibling is filed")
        pieces.append((name, *piece))
    lineage: dict = {}
    if pieces:
        try:
            lineage = _lineage(card)
        except Exception as exc:  # noqa: BLE001
            return _finish(card, writes, f"the card's parent could not be read ({exc})")
    because = split_finding(found)

    # Every sibling is filed before the card is narrowed: a refused filing
    # then leaves the card whole, never a piece that is in neither place.
    filed: list[tuple[str, str]] = []
    for i, (name, title, body) in enumerate(pieces):
        try:
            ident = writes.do(f"file {name}",
                              lambda: _file_sibling(lineage, title, body, because),
                              named=lambda made, name=name: f"file {name} as {made}")
        except _Stopped as stop:
            raise _Stopped(f"{stop.what} — not filed: "
                           + ", ".join(left for left, *_ in pieces[i:])) from stop
        filed.append((ident, title))
    if revised.strip():
        writes.do(W_DESCRIPTION, lambda: linear_ops.set_description(card, revised))
        writes.do(W_DECISIONS, lambda: _decisions_block(card))
    if shape:
        writes.do(W_STAMP, lambda: _stamp_epic(card, because, model))
    _lift(writes, card, reason, held)
    writes.do(W_MOVE, lambda: _move_to_planning(card))
    writes.do(W_RECEIPT, lambda: _post(card, split_body(found, answers, filed,
                                                        bool(shape))))
    print(f"triage-repair: {card} → {SPLIT}")
    return EXIT_OK


# --------------------------------------------------------------------------- #
# CLI                                                                          #
# --------------------------------------------------------------------------- #


def _cmd_select(args) -> int:
    rows = select(args.limit)
    with open(args.out, "w", encoding="utf-8") as fh:
        json.dump(rows, fh, ensure_ascii=False, indent=2)
        fh.write("\n")
    return EXIT_OK


def _cmd_claim(args) -> int:
    try:
        return claim(args.card)
    except Exception as exc:  # noqa: BLE001 — unread or unposted: no claim stands
        print(f"ERROR: {args.card}: the claim could not be made ({exc})", file=sys.stderr)
        return EXIT_UNFINISHED


def _cmd_apply(args) -> int:
    return apply(args.card, args.action, args.dir, args.model)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("select", help="decide each card's repair; writes nothing")
    s.add_argument("--out", required=True)
    s.add_argument("--limit", type=int, default=DEFAULT_LIMIT)
    s.set_defaults(fn=_cmd_select)

    c = sub.add_parser("claim", help="post the started receipt, once per card")
    c.add_argument("card")
    c.set_defaults(fn=_cmd_claim)

    a = sub.add_parser("apply", help="apply what the planner wrote")
    a.add_argument("card")
    a.add_argument("--action", required=True, choices=APPLIED)
    a.add_argument("--dir", default=None)
    a.add_argument("--model", default=None,
                   help="the model the planner ran on, for a split's epic stamp")
    a.set_defaults(fn=_cmd_apply)

    args = parser.parse_args(argv)
    return args.fn(args)


if __name__ == "__main__":
    sys.exit(main())
