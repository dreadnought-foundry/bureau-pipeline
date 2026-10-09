#!/usr/bin/env python3
"""The sweep dispatches a proof run at a PROOF card (DRE-5926).

When an epic's last build card is Done and the release carrying its merges is
live, this phase starts the proof run at the epic's `PROOF:` card, the way the
sweep starts builds. Nothing did before: a PROOF card routes OPERATOR, lands in
`Hand-work` carrying `no-code` (`routing_verdict.card_marks`), and `Hand-work`
is not a lane the sweep's nudge loop reads (`reconcile.SWEEP_STATES`), so the
card waited for a person. On 2026-10-05, 13 of the 17 epics counted against the epic cap
were waiting on exactly that.

Its own module and its own step in `reconcile.yml` (`Dispatch proof runs`,
after `Sweep`), because `reconcile.py` is past eleven thousand lines and an
edit to a file that size is what killed DRE-3088 three times. The step runs on
full passes only and only where the repo carries a proof-run stub (`STUBS`) —
both gates are in the workflow and cost no Linear read.

THIS PHASE DECIDES; IT DOES NOT READ. Whether the release is live is
`proof_release.reading`, what became of a run is `proof_run_state.reading`
(both DRE-5922), the event is `plan_run.PROOF_EVENT` (DRE-5921), and whose
comment is the CEO's signed answer is `spoken_thread.voices`. None of them is
re-derived here.

## The lanes

A first-run candidate is in `Hand-work`, and it stays there for the whole run
(DRE-5924): what keeps this phase from dispatching twice at a running card is
the run-state reading (condition 5), not a lane. A card in `Green Light` is
read by the return branch when it is parked on the CEO's press, and by the
re-run branch when its hold is the sweep's review-cap park (DRE-6406) — never
by both: the return is taken first. A card in `In Review` has an open record
pull request and is read only by the re-run branch. The re-run branch moves
nothing: the card stays in the lane it is in while its record is amended.

## A first run — every condition read, in order; the first that fails is named

  1. A `PROOF:` title, a `repo:` label naming this repo, in `Hand-work`.
  2. Its parent epic is `In Progress`.
  3. Every `blocks` relation on it is terminal (`prose_blockers`).
  4. It is not held: no `needs-human`, and no `🔬 proof-waiting` hold the
     thread has not discharged — by a later `🔬 proof-observed` line, or, for
     a hold naming `the CEO's press`, by his signed answer after it.
  5. Nobody else is on it and its run is not alive: only `none`,
     `never-started` and `dead` go on.
  6. The first-run budget: two dispatches. The second names why the first
     did not finish; after two, one hold, and never again.
  7. The release carrying the siblings' merges is `ready` — `waiting` and
     `unknown` both wait.

One exception to the order, and it costs nothing (DRE-6464): condition 4 is
read first, off the lane read, where the lane read already answers it — a
`needs-human` label, or a `🔬 proof-waiting` hold the card's comment window
shows with no `🔬 proof-observed` line and no console answer after it. That
card is named on its line every pass and spends none of the candidate reads.
A hold the window shows something after, or does not show at all, is the
thread read's to decide, in order, as before.

## The return after the CEO's answer

A `Green Light` PROOF card whose newest hold names `the CEO's press`, with a
signed answer after it and no `🔬 proof-run` receipt after the answer, is
dispatched again: once per signed answer. Conditions 2 and 3 are read for it;
5, 6 and 7 are not — its record is open by design, the first-run budget is not
its budget, and the release held at its first dispatch. Only a run in flight
(`running`, or `unknown`, which is never read as free) holds it. Its lane
moves are DRE-5925's, in `proof-task.yml`; this phase moves nothing.

## The re-run after the critic's findings (DRE-5931)

An `In Review` PROOF card whose open pull request is on its proof-record
branch (`proof_record_branch`) and whose newest critic verdict at the head is
`REQUEST_CHANGES` (`reconcile.critic_comments` / `standing_verdict`) is
dispatched once more, `re-run after the critic's findings at <sha7>`, when no
`🔬 proof-run` receipt is newer than that verdict (the findings are
unanswered), or the newest is and its run reads `dead` or `never-started` (the
re-run died, or never began, before it amended the record). The run resumes
its branch and amends the record, so the critic reads it again on the same
pull request. A newer receipt whose run reads `finished` with the verdict
still at the head amended nothing the critic could read: one hold, for an
operator, never a guess at another run. Three things stop it, each
named: a `🔬 proof-waiting` hold nothing discharged, a run `running` or
`unknown`, and the budget — two re-runs per pull request, counted off the
receipts whose count opens `re-run` posted after it opened, apart from the
first-run budget, so a re-run that never began still spends one. After two,
one hold, and the card is left for an operator.
Conditions 2, 3 and 7 are not read for it: the record is open on the release
its first run read.

## The re-run after the gate's decline (DRE-6488)

A record the critic APPROVED can still be held: the merge gate declines any
proof record with a row not met (DRE-6141), and before this nothing picked it
up again. The same branch, the same lanes and the same stops read a second
trigger, only when the critic's standing verdict at the head is `APPROVE`
(its `REQUEST_CHANGES` keeps precedence): the gate's newest hold note on the
pull request — qa-bot only, anchored on its first line, as
`reconcile.gate_hold_note_line` reads it — declining the record at the head
with a reason that opens `proof record not proven:`. A note on an earlier
head, or with any other reason, is not it. The rows are read off the record
at the head (`proof_record.fetch` over the pull request's `files`), never off
the note; a record not read there is refused. Then, after the stops:

  1. A row reading `Not observed. waiting for <the event>`
     (`proof_record.row_waiting`) holds the card on one `🔬 proof-waiting`
     naming the waiting rows and their events, and nothing is dispatched. The
     operator's `🔬 proof-observed` discharges it, and the next pass re-runs.
  2. Otherwise the run is dispatched, `re-run after the gate's decline at
     <sha7>`, under the same rule for a newer receipt as the critic's.
  3. The one budget: two re-runs per record pull request, whichever trigger
     spent them. After two, one hold; a re-run that finished with the decline
     still at the head, one hold. Never a third guess.

A `Green Light` PROOF card is read the same way when its live hold is the
sweep's review-cap park — `needs-human` with a `🔒 hold: reason=review-cap-spent`
stamp, read off the lane read's window, the whole thread when the window is
partial (DRE-6406). A record parked there before the sweep stood down on a
sent-back record waits on nothing else. One more refusal is named for it: the
stamp's `at` must be the record's head, because on a newer head the holds lane
lifts the stamp and returns the card to `In Review` itself. Nothing here moves
the card, lifts the stamp or touches the label: the re-run's amended record is
the new head, and the holds lane does the rest. A label over a spent stamp, or
over none, is a person's hold and is not read.

## The bound

At most one dispatch per pass — the return first, then first runs oldest
first and re-runs after them, as one ring begun at the pass's turn — and at
most `PROOF_CANDIDATES_PER_PASS` candidates read:
two Linear reads for the three lanes (the sweep's board read serves
`Hand-work` and `In Review` together, and one `Green Light` read serves the
return and the re-run), then at most two per candidate (the
card's epic and relations, and its thread; a re-run reads only its thread).
Three candidates is 2 + 2 × 3 = 8 requests however many proofs wait — plus,
for a card whose first relation page is full, up to
`reconcile.INVERSE_TOPUP_PAGES` more to read the rest of it (DRE-6416). Its
`linear-budget:` trailer is its own, lifted into the step summary.

THE TURN (DRE-6464). The bound once read the same three every pass: oldest
first, a card held, blocked or unreadable kept its slot, and on 2026-10-09
three of them kept DRE-6042 — whose claim is that the sweep starts it with no
person — out of every pass until Sunday. The phase writes nothing for a card
it refuses, so a pass cannot know what the last one read; it knows the clock.
Each `PASS_MINUTES` turn begins the ring at its own place on it (`_turned`):
the turn's golden-ratio fraction of the ring, so consecutive turns step about
0.618 of the way round and a ring of up to four is read whole within two
passes. A fixed stride of three was the first answer and it was wrong: with
passes landing every second turn — a cron that only fires at :07 and :37 — a
ring of six or twelve read the same windows forever. The golden step is odd
in the turn, so on any steady cadence of whole turns, every turn or every
fourth alike, every place on the ring comes up and every candidate is read.
A pass that runs late, twice in a turn or not at all only changes which turn
it reads. The bound is unchanged.

## The dry run

Unless `PROOF_DISPATCH_LIVE` is exactly `true`, the phase prints `would:`
lines and writes nothing — no dispatch, no receipt, no hold.

CLI (the step's own call; reads `REPO`, `REPO_SLUG`, `PROOF_DISPATCH_LIVE`):

    proof_dispatch.py
"""

from __future__ import annotations

import json
import os
import re
import sys
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Callable

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import card_pr  # noqa: E402
import hold  # noqa: E402 — the sweep's review-cap stamp, read as the holds lane reads it
import linear_ops  # noqa: E402
import merge_gate  # noqa: E402 — the critic's marker for `standing_verdict`
import pipeline_act  # noqa: E402
import plan_run  # noqa: E402
import proof_and_demo  # noqa: E402
import proof_record  # noqa: E402 — the record at the head, read as the gate reads it
import proof_release  # noqa: E402
# The branch rule and the hold reader live once, in the leaf the merge gate and
# the PROOF close read too (DRE-6141) — moved, not copied.
from proof_record import (  # noqa: E402,F401 — the names this phase and its callers read
    CEO_PRESS,
    HOLD_MARK,
    OBSERVED_MARK,
    proof_record_branch,
)
from proof_record import open_holds as _open_holds  # noqa: E402
import proof_run_state  # noqa: E402
import prose_blockers  # noqa: E402
import reconcile  # noqa: E402 — the lane read, the repo label, the GitHub read seam
import spoken_thread  # noqa: E402

#: Opens every line this phase prints.
PREFIX = "proof-dispatch:"

#: The receipt's tag — the registry's `proof-run-dispatched` row adopts it
#: off this line, and `proof_run_state.RECEIPT_MARKER` reads it back.
PROOF_RUN_TAG = "proof-run"

#: How many candidates one pass reads, from its turn's place in the queue;
#: the rest wait for a later pass.
PROOF_CANDIDATES_PER_PASS = 3

#: The sweep's cadence — the stubs' `*/15` cron — which numbers a pass's
#: turn. The phase writes nothing for a card it refuses, so the clock is the
#: only thing one pass shares with the last (DRE-6464).
PASS_MINUTES = 15

#: The repository variable that turns the dry run off — `true` and nothing else.
LIVE_VARIABLE = "PROOF_DISPATCH_LIVE"

#: The proof-run stubs the step tests for before it runs this file, in
#: `reconcile.fix_workflow()`'s naming family: bureau-pipeline's own stub is
#: `self-proof-task.yml`, a product repo's is `proof-task.yml`.
STUBS = (".github/workflows/self-proof-task.yml", ".github/workflows/proof-task.yml")

#: What the step prints when neither stub is there, with no Python run.
NO_STUB = ("proof-dispatch: no proof-run stub in this repository — nothing "
           "read, nothing dispatched")

FIRST_RUN_LANE = "Hand-work"
RETURN_LANE = "Green Light"
RERUN_LANE = "In Review"
EPIC_ACTIVE = "In Progress"

#: The first-run budget, and the run states that leave a card free for it.
FIRST_RUN_BUDGET = 2
FREE = ("none", "never-started", "dead")
#: The run states that hold the return: a run in flight, or one unreadable.
IN_FLIGHT = ("running", "unknown")

FIRST_REASON = "first proof run"
NEVER_STARTED_REASON = "second dispatch — no run started after {at}"
DEAD_REASON = "second dispatch — {run}"
RETURN_REASON = "re-run after the CEO's answer at {at}"
FIRST_COUNT = "dispatch {n} of 2"
RETURN_COUNT = "after the CEO's answer"
RERUN_REASON = "re-run after the critic's findings at {sha7}"
GATE_RERUN_REASON = "re-run after the gate's decline at {sha7}"
RERUN_COUNT = "re-run {n} of 2"

#: The re-run budget: per record pull request, apart from the first run's.
RERUN_BUDGET = 2
#: What the critic's newest verdict at the head must say for a re-run.
SENT_BACK = "REQUEST_CHANGES"
#: ...and for the gate's trigger (DRE-6488): the critic approved the record,
#: and the gate declined it at the same head.
APPROVED = "APPROVE"
#: The reason the merge gate writes when it holds a proof record
#: (`merge_gate.evaluate_proof_record`). Any other reason is not the trigger.
NOT_PROVEN = "proof record not proven:"
#: The one hold a `Green Light` re-run candidate carries: the sweep's park at
#: its review cap (DRE-6406). A person's label, or any other reason, is not it.
REVIEW_CAP = "review-cap-spent"

#: The hold after two first-run dispatches that did not finish.
EXHAUSTED_OBSERVED = "the proof run did not finish after two dispatches"
EXHAUSTED_NEEDS = ("an operator reading the two 🔬 proof-run receipts and the "
                   "Actions runs they name")

#: The hold after two re-runs the critic sent back again.
RERUN_EXHAUSTED_OBSERVED = "the record was sent back twice after re-observation"
RERUN_EXHAUSTED_NEEDS = ("an operator reading the critic's findings and the two "
                         "re-run receipts")
#: The run states that leave a re-run newer than the verdict owed again: it
#: died, or it never began. Either way its receipt still spends the budget.
RERUN_AGAIN = ("dead", "never-started")
#: The hold after a re-run that finished with the verdict still at the head:
#: it amended nothing the critic could read, and sending it again is a guess.
RERUN_UNANSWERED_OBSERVED = ("the re-run finished and the critic's findings "
                             "still stand at the record's head")
RERUN_UNANSWERED_NEEDS = ("an operator reading the critic's findings and the "
                          "re-run's thread")
#: The same two holds when the gate's decline is what stands (DRE-6488).
GATE_EXHAUSTED_OBSERVED = "the gate declined the record twice after re-observation"
GATE_EXHAUSTED_NEEDS = ("an operator reading the gate's declined note and the two "
                        "re-run receipts")
GATE_UNANSWERED_OBSERVED = ("the re-run finished and the gate's decline still "
                            "stands at the record's head")
GATE_UNANSWERED_NEEDS = ("an operator reading the gate's declined note and the "
                         "re-run's thread")

#: What the sweep's promotion posts as a card lands in `Hand-work` — the one
#: time on the lane read that says when the card entered the lane.
PROMOTED_MARK = f"🧹 Auto-promoted Backlog → {FIRST_RUN_LANE}"

#: The fields the condition-7 lookup needs; `mergeCommit` is the merge's sha.
PR_FIELDS = "number,url,headRefName,state,mergeCommit"
#: The fields the re-run reads off the record pull request: its head, when it
#: opened, the comments the critic's verdict and the gate's note are read
#: from, and the files the record is found among (DRE-6488).
RECORD_FIELDS = "number,url,headRefName,state,headRefOid,createdAt,comments,files"

#: The card's epic, its siblings and its blocking relations, in one read.
CARD_QUERY = """query($id: String!) { issue(id: $id) {
             %s
             parent { identifier state { name }
               children(first: 100) { nodes {
                 identifier title state { name } labels { nodes { name } } } } }
           } }""" % reconcile.INVERSE_RELATIONS_GQL

_DEAD = re.compile(r"^dead — (run \S+ ended .+? with no record)")
#: The gate's hold note's first line (`evaluate_and_merge.sh`):
#: `⏸️ Merge gate: declined @<sha> — <reason>`.
_DECLINED = re.compile(rf"{reconcile.GATE_HOLD_NOTE_MARKER} @(?P<sha>[0-9a-f]+) — (?P<reason>.*)")


def _when(stamp) -> datetime | None:
    try:
        return datetime.fromisoformat(str(stamp or "").replace("Z", "+00:00"))
    except ValueError:
        return None


def is_live() -> bool:
    return os.environ.get(LIVE_VARIABLE) == "true"


# --------------------------------------------------------------------------- #
# The reads — injected in the tests, these in production                       #
# --------------------------------------------------------------------------- #


class LinearReads:
    """The three Linear reads this phase makes, and nothing else."""

    def lane(self, state: str) -> list:
        """A lane through the sweep's own read: the read door serves it where
        it is on, Linear where it is not."""
        return reconcile.active_cards((state,))

    def card(self, identifier: str) -> dict:
        issue = (linear_ops.gql(CARD_QUERY, {"id": identifier}) or {}).get("issue")
        if not issue:
            raise LookupError(f"Linear answered no card for {identifier}")
        # A proof is blocked by every other card in its epic, so a big epic's
        # fills the first page: read it to the end, as the sweep does
        # (DRE-6416). A failed read leaves it UNKNOWN, and condition 3 refuses.
        issue.setdefault("identifier", identifier)
        reconcile.complete_inverse_relations([issue])
        return issue

    def thread(self, identifier: str):
        return linear_ops._thread_and_viewer(identifier, "body", "user",
                                             "createdAt", whole=True)


def github_read(path: str):
    """`gh api <path>` as JSON through the sweep's read seam — the pool App's
    hour (`GH_READ_TOKEN`), retried on a brief refusal, raising on failure."""
    text = reconcile.gh_read("api", path)
    return json.loads(text) if text else None


def merged_pr(identifier: str, repo: str) -> dict | None:
    """The card's newest counting pull request on `repo`, as `hygiene_done`
    reads it."""
    return card_pr.find(identifier, repo=repo, fields=PR_FIELDS,
                        run=lambda args: reconcile.gh_read(*args))


def record_at(repo: str, pr: dict, head: str) -> proof_record.Record:
    """The record the pull request adds, read at `head` — the reader and the
    finder the merge gate uses, through the sweep's GitHub read seam. Never
    raises: a record not found or not read has no text and says why."""
    return proof_record.fetch(repo, pr.get("files"), head,
                              gh=lambda args: reconcile.gh_read(*args))


def record_pr(identifier: str, repo: str) -> dict | None:
    """The card's newest counting pull request on `repo`, its record branch
    asked for first, with what the re-run reads off it."""
    return card_pr.find(identifier, branch=f"agent/{identifier}-proof-record",
                        repo=repo, fields=RECORD_FIELDS,
                        run=lambda args: reconcile.gh_read(*args))


# --------------------------------------------------------------------------- #
# The pass                                                                     #
# --------------------------------------------------------------------------- #


@dataclass
class Tally:
    eligible: int = 0
    dispatched: int = 0
    waiting: int = 0
    held: int = 0
    someone_else: int = 0
    running: int = 0
    deferred: int = 0
    refused: int = 0
    failures: list = field(default_factory=list)

    def line(self, live: bool) -> str:
        return (f"{PREFIX} eligible {self.eligible}, dispatched "
                f"{self.dispatched}, waiting on release {self.waiting}, held "
                f"{self.held}, someone else's {self.someone_else}, running "
                f"{self.running}, deferred {self.deferred}, refused "
                f"{self.refused} ({'live' if live else 'dry run'})")


class _Refused(Exception):
    """A condition failed: `bucket` is the tally it counts in."""

    def __init__(self, line: str, bucket: str = "refused"):
        super().__init__(line)
        self.line, self.bucket = line, bucket


def _say(identifier: str, text: str) -> None:
    print(f"{PREFIX} {identifier} — {text}")


def _condition(n: int, name: str, why: str, bucket: str = "refused") -> _Refused:
    return _Refused(f"condition {n} ({name}): {why}", bucket)


def _pt(when: datetime) -> str:
    return spoken_thread.pacific_label(when.isoformat())


def _labels(card: dict) -> list:
    return [(lbl.get("name") or "").lower()
            for lbl in (card.get("labels") or {}).get("nodes") or []]


def _first_line(text: str | None) -> str:
    return (text or "").strip().split("\n", 1)[0].strip()


def _ours(card: dict, slug: str) -> bool:
    return (proof_and_demo.is_proof(card.get("title") or "")
            and reconcile.card_repo(card) == slug)


def _entered(card: dict) -> str:
    """When the card entered `Hand-work`, off the lane read: the promotion's
    own receipt, else the card's last update. ISO strings sort as times."""
    window = linear_ops.window_nodes(card.get("comments"))
    stamps = [c.get("createdAt") or "" for c in window
              if (c.get("body") or "").startswith(PROMOTED_MARK)]
    return (stamps[-1] if stamps else "") or card.get("updatedAt") or ""


def _gate_hold_note(pr: dict) -> dict | None:
    """The gate's newest hold note on the pull request, with its time — read
    as `reconcile.gate_hold_note_line` reads it: qa-bot only and anchored on
    the first line, so a person quoting the note is not it."""
    for comment in reversed(pr.get("comments") or []):
        if (reconcile.is_qa_bot_comment(comment) and merge_gate.opens_with_marker(
                comment.get("body") or "", reconcile.GATE_HOLD_NOTE_MARKER)):
            return comment
    return None


@dataclass(frozen=True)
class _Trigger:
    """What stands at the record's head and sends it back to the proof run:
    the critic's findings (DRE-5931) or the gate's decline (DRE-6488)."""
    at: datetime        # when it was posted; a receipt after it answers it
    reason: str         # the dispatch reason, before `sha7`
    stands: str         # what still stands, for the lines
    answer: tuple       # what a newer receipt answered, and what it is newer than
    again: str          # what the budget line says happened again
    exhausted: tuple    # the hold after two re-runs
    unanswered: tuple   # the hold after a re-run that finished
    gate: bool = False  # the record's rows are read only for the gate's


def _number(card: dict) -> int:
    digits = (card.get("identifier") or "").rsplit("-", 1)[-1]
    return int(digits) if digits.isdigit() else 0


def _turn(now: datetime) -> int:
    """This pass's number on the sweep's clock."""
    return int(now.timestamp() // (PASS_MINUTES * 60))


#: ⌊2³² / φ⌋, odd — Knuth's multiplicative hash: a turn's golden-ratio
#: fraction of the ring, in 32 bits.
_GOLDEN = 2654435769


def _turned(ring: list, now: datetime) -> list:
    """`ring` begun at this turn's place on it (DRE-6464), so the candidates
    one pass read and refused are not the next pass's front. The place is the
    turn's golden-ratio fraction of the ring: consecutive turns step about
    0.618 of the way round, so a ring of up to four is read whole within two
    passes, whatever the turn. Because the multiplier is odd, on a steady
    cadence of k turns the start takes every 32-bit fraction a multiple of
    2^v apart (2^v the largest power of two in k), so every place on a ring
    under 2³² / k comes up — every turn, every second or every fourth alike.
    A fixed stride of three did not: at every second turn a ring of six or
    twelve read the same windows forever."""
    if not ring:
        return ring
    start = (_turn(now) * _GOLDEN % 2 ** 32) * len(ring) >> 32
    return ring[start:] + ring[:start]


#: The voices after a hold that cannot discharge it: anything else — a
#: `🔬 proof-observed` line or a console answer of any reading — leaves the
#: hold to the thread read.
_INERT = (spoken_thread.PIPELINE, spoken_thread.PERSON, spoken_thread.UNKNOWN)


def _answered_after_park(voices: list) -> tuple | None:
    """`(index, voice)` of his newest signed answer after the newest
    `🔬 proof-waiting` hold, when that hold names the CEO's press — else None."""
    holds = [i for i, v in enumerate(voices)
             if (v.body or "").lstrip().startswith(HOLD_MARK)]
    if not holds or CEO_PRESS not in _first_line(voices[holds[-1]].body):
        return None
    answers = [(i, v) for i, v in enumerate(voices)
               if i > holds[-1] and v.kind == spoken_thread.CEO_VIA_CONSOLE]
    return answers[-1] if answers else None


def _unchecked_after_hold(voices: list) -> bool:
    """Does a console answer the check could not RUN on follow the newest
    hold? `spoken_thread.UNCHECKED` is neither his answer nor a refused one
    (DRE-4153): it discharges nothing and returns nothing, and the line says
    so, rather than reading as a thread with no answer at all."""
    holds = [i for i, v in enumerate(voices)
             if (v.body or "").lstrip().startswith(HOLD_MARK)]
    return bool(holds) and any(v.kind == spoken_thread.UNCHECKED
                               for v in voices[holds[-1] + 1:])


UNCHECKED_NOTE = ("a console answer after it COULD NOT BE CHECKED (the "
                  "console's key could not be read) — it counts for nothing "
                  "until a pass that can check it")


class _Pass:
    def __init__(self, repo, slug, *, live, linear, read, find_pr, run_state,
                 release, fire, voices, now, find_record, read_record):
        self.repo, self.slug, self.live = repo, slug, live
        self.linear, self.read, self.find_pr = linear, read, find_pr
        self.find_record, self.read_record = find_record, read_record
        self.run_state, self.release, self.fire = run_state, release, fire
        self.voices, self.now = voices, now
        self.tally = Tally()

    # -- the reads every candidate shares (conditions 2 and 3) ------------- #

    def _epic_and_blockers(self, card: dict) -> dict:
        ident = card["identifier"]
        try:
            issue = self.linear.card(ident)
        except Exception as error:  # noqa: BLE001 — unread is never eligible
            raise _condition(2, "epic", f"the card's epic and relations could "
                                        f"not be read: {error}")
        parent = issue.get("parent") or {}
        epic_state = (parent.get("state") or {}).get("name")
        if not parent:
            raise _condition(2, "epic", "the card has no parent epic")
        if epic_state != EPIC_ACTIVE:
            raise _condition(2, "epic", f"its epic {parent.get('identifier')} "
                                        f"is {epic_state}, not {EPIC_ACTIVE}")
        if prose_blockers.relations_unknown(issue):
            raise _condition(3, "blockers", "its blocking relations could not "
                                            "be read to the end")
        open_ = prose_blockers.relation_blockers(issue)
        if open_:
            states = prose_blockers.blocker_states(issue)
            named = ", ".join(f"{b} is {states[b]}" for b in sorted(open_))
            raise _condition(3, "blockers", f"not every blocker is terminal — {named}")
        return issue

    def _thread(self, card: dict):
        ident = card["identifier"]
        try:
            comments, viewer = self.linear.thread(ident)
            voices = self.voices(comments, viewer, card=ident)
        except Exception as error:  # noqa: BLE001
            raise _condition(4, "hold", f"the thread could not be read, so no "
                                        f"hold can be ruled out: {error}")
        return comments, viewer, voices

    # -- a first run -------------------------------------------------------- #

    def held_on_the_lane(self, card: dict) -> str | None:
        """Condition 4 off the lane read, at no request (DRE-6464): a
        `needs-human` label, or a `🔬 proof-waiting` hold the card's comment
        window shows with nothing after it that could discharge it — the
        refusal line `first_run` would name. None leaves the card to the
        reads. The window is the newest comments, so anything after a hold it
        shows is in it; a hold older than the window is the reads' to find."""
        if "needs-human" in _labels(card):
            return "it carries needs-human"
        window = linear_ops.window_nodes(card.get("comments"))
        try:
            voices = self.voices(window, None, card=card["identifier"])
        except Exception:  # noqa: BLE001 — unread rules nothing out
            return None
        marks = [i for i, v in enumerate(voices)
                 if (v.body or "").lstrip().startswith(HOLD_MARK)]
        if not marks:
            return None
        if any(v.kind not in _INERT
               or (v.body or "").lstrip().startswith(OBSERVED_MARK)
               for v in voices[marks[-1] + 1:]):
            return None
        return f"held by {_first_line(voices[marks[-1]].body)}"

    def first_run(self, card: dict) -> tuple:
        """`(reason, count)` for an eligible first-run candidate, or raises
        `_Refused` naming the first condition that fails."""
        ident = card["identifier"]
        issue = self._epic_and_blockers(card)
        if "needs-human" in _labels(card):
            raise _condition(4, "hold", "it carries needs-human", "held")
        comments, viewer, voices = self._thread(card)
        holds = _open_holds(voices)
        if holds:
            note = f"; {UNCHECKED_NOTE}" if _unchecked_after_hold(voices) else ""
            raise _condition(4, "hold", f"held by {holds[-1]}{note}", "held")

        got = self.run_state(self.repo, ident, comments, viewer,
                             read=self.read, now=self.now)
        why = "; ".join(got.lines)
        if got.state == "someone-else":
            raise _condition(5, "run", why, "someone_else")
        if got.state not in FREE:
            raise _condition(5, "run", why, "running")

        reason = self._budget(card, got, voices)
        self._released(issue, ident)
        return reason, FIRST_COUNT.format(n=got.dispatches + 1)

    def _budget(self, card: dict, got, voices: list) -> str:
        """Condition 6: the reason this dispatch spends, or the refusal."""
        ident = card["identifier"]
        if got.dispatches >= FIRST_RUN_BUDGET:
            self._hold_once(ident, voices, EXHAUSTED_OBSERVED, EXHAUSTED_NEEDS)
            raise _condition(6, "budget", f"{got.dispatches} first-run "
                                          "dispatches and none finished — "
                                          "never dispatched again by this "
                                          "phase", "held")
        if got.dispatches == 0 or got.state == "none":
            return FIRST_REASON
        if got.state == "never-started":
            at = got.receipts[-1].at if got.receipts else "an unknown time"
            return NEVER_STARTED_REASON.format(at=at)
        found = next((m for line in got.lines if (m := _DEAD.match(line))), None)
        run = found.group(1) if found else f"run {got.run_id} ended with no record"
        return DEAD_REASON.format(run=run)

    def _hold(self, ident: str, observed: str, needs: str) -> None:
        if not self.live:
            print(f"would: hold {ident} — {observed}")
            return
        try:
            linear_ops.cmd_proof_waiting(ident, observed, needs)
        except Exception as error:  # noqa: BLE001
            self.tally.failures.append(f"{ident}: the hold could not be posted: {error}")
            _say(ident, f"ERROR: the hold could not be posted: {error}")

    def _hold_once(self, ident: str, voices: list, observed: str,
                   needs: str) -> None:
        """`_hold`, unless the thread already carries this exact hold line."""
        line = linear_ops.proof_waiting_line(observed, needs)
        if not any(_first_line(v.body).startswith(line) for v in voices):
            self._hold(ident, observed, needs)

    def _released(self, issue: dict, ident: str) -> None:
        """Condition 7: the release carrying the siblings' merges is live."""
        siblings = [c for c in ((issue.get("parent") or {}).get("children")
                                or {}).get("nodes") or []
                    if c.get("identifier") != ident
                    and not proof_and_demo.is_proof(c.get("title") or "")]
        merges = []
        for sibling in siblings:
            other = sibling["identifier"]
            if (sibling.get("state") or {}).get("name") not in prose_blockers.TERMINAL:
                continue
            slug = reconcile.card_repo(sibling)
            if slug != self.slug:
                _say(other, f"unchecked — its repo: label names {slug or 'no repo'}, "
                            f"not {self.slug}; its release is not this repo's to read")
                continue
            try:
                pr = self.find_pr(other)
            except Exception as error:  # noqa: BLE001 — unread is never ready
                raise _condition(7, "release", f"unknown — the pull request "
                                               f"for {other} could not be read: "
                                               f"{error}")
            if card_pr.pr_state(pr) != card_pr.MERGED:
                continue
            sha = (pr.get("mergeCommit") or {}).get("oid")
            if not sha:
                raise _condition(7, "release", f"unknown — #{pr.get('number')} "
                                               f"for {other} names no merge commit")
            merges.append(proof_release.Merge(other, pr["number"], sha, []))
        got = self.release(self.repo, merges, read=self.read)
        if got.state != "ready":
            raise _condition(7, "release", "; ".join(got.lines),
                             "waiting" if got.state == "waiting" else "refused")

    # -- the return after the CEO's answer ---------------------------------- #

    def is_return(self, card: dict) -> bool:
        """Off the lane read's own comment window, at no request: a PROOF card
        of this repo whose window holds his signed answer after a park naming
        his press. The thread read decides; this only spares the read."""
        if not _ours(card, self.slug):
            return False
        window = linear_ops.window_nodes(card.get("comments"))
        try:
            voices = self.voices(window, None, card=card["identifier"])
        except Exception as error:  # noqa: BLE001 — unread is no answer
            _say(card["identifier"], f"return: its comments could not be read "
                                     f"for his answer: {error}")
            return False
        if (_answered_after_park(voices) is not None
                or _unchecked_after_hold(voices)):
            return True
        return (linear_ops.window_is_partial(card.get("comments"))
                and any(v.kind == spoken_thread.CEO_VIA_CONSOLE for v in voices))

    def returning(self, card: dict) -> tuple:
        ident = card["identifier"]
        self._epic_and_blockers(card)
        comments, viewer, voices = self._thread(card)
        found = _answered_after_park(voices)
        if found is None and _unchecked_after_hold(voices):
            raise _Refused(f"return: the park has {UNCHECKED_NOTE}", "held")
        if found is None:
            raise _Refused("return: no signed answer of the CEO's follows a "
                           "park naming his press")
        index, voice = found
        later = [v for v in voices[index + 1:]
                 if v.kind == spoken_thread.PIPELINE
                 and proof_run_state.receipt(v.body or "") is not None]
        if later:
            raise _Refused("return: a proof-run receipt already follows his "
                           f"answer posted {spoken_thread.pacific_label(voice.created_at)} "
                           "— one dispatch per signed answer")
        got = self.run_state(self.repo, ident, comments, viewer,
                             read=self.read, now=self.now)
        if got.state in IN_FLIGHT:
            raise _Refused(f"return: a run is in flight — {'; '.join(got.lines)}",
                           "running")
        at = spoken_thread.pacific_label(voice.created_at)
        return RETURN_REASON.format(at=at), RETURN_COUNT

    # -- the re-run after the critic's findings (DRE-5931) ------------------ #

    def is_rerun(self, card: dict) -> bool:
        """A PROOF card of this repo in `In Review` — or in `Green Light`
        under the sweep's review-cap park (DRE-6406) — at no request.

        The park is read off the lane read's own comment window: the label
        with a live `review-cap-spent` stamp. A label over a spent stamp, or
        over none, is a person's hold and is not read — unless the window is
        partial and the stamp may lie beyond it, when the whole thread
        decides (`rerunning`), as it does for `is_return`."""
        if not _ours(card, self.slug):
            return False
        lane = (card.get("state") or {}).get("name")
        if lane == RERUN_LANE:
            return True
        if lane != RETURN_LANE:
            return False
        window = linear_ops.window_nodes(card.get("comments"))
        reason = hold.reason_of((card.get("labels") or {}).get("nodes") or [],
                                [c.get("body") or "" for c in window])
        return reason == REVIEW_CAP or (
            reason == "manual" and linear_ops.window_is_partial(card.get("comments")))

    def _parked_on(self, card: dict, comments: list, number, head: str) -> None:
        """For a `Green Light` card, the one refusal an `In Review` card does
        not have: the sweep's review-cap stamp, read off the whole thread,
        must be on the record's current head. On an older head the holds lane
        lifts it and returns the card to `In Review` (`hygiene_holds.RETURNS`)
        — that card is its, and nothing is dispatched here."""
        if (card.get("state") or {}).get("name") != RETURN_LANE:
            return
        bodies = [c.get("body") or "" for c in comments]
        labels = (card.get("labels") or {}).get("nodes") or []
        if hold.reason_of(labels, bodies) != REVIEW_CAP:
            raise _Refused("re-run: its Green Light hold is not the sweep's "
                           "review-cap park — a person's, not read here")
        at = (hold.read_stamp(bodies) or {}).get("at") or ""
        if at != head:
            raise _Refused(f"re-run: the review-cap park is on {at[:7]}, and "
                           f"#{number}'s head is now {head[:7]} — the holds lane "
                           "lifts it on that new head and returns the card to "
                           f"{RERUN_LANE}; nothing dispatched", "held")

    def rerunning(self, card: dict) -> tuple:
        """`(reason, count)` for a record the critic sent back, or raises
        `_Refused` naming why nothing is dispatched."""
        ident = card["identifier"]
        try:
            pr = self.find_record(ident)
        except Exception as error:  # noqa: BLE001 — unread is never sent back
            raise _Refused(f"re-run: the record pull request could not be "
                           f"read: {error}")
        if card_pr.pr_state(pr) != card_pr.OPEN:
            raise _Refused("re-run: no open record pull request — nothing for "
                           "the critic to send back")
        number, branch = pr.get("number"), pr.get("headRefName")
        if not proof_record_branch(branch):
            raise _Refused(f"re-run: #{number} is on {branch}, not a "
                           "proof-record branch")
        head = pr.get("headRefOid") or ""
        verdicts = reconcile.critic_comments(pr)
        standing = reconcile.standing_verdict(verdicts, merge_gate.CRITIC_MARKER,
                                              head)
        if standing == SENT_BACK:
            trigger = self._sent_back(verdicts, number)
        elif standing == APPROVED:
            trigger = self._declined(pr, number, head)
        else:
            raise _Refused(f"re-run: the critic's newest verdict on #{number} "
                           f"at {head[:7]} is {standing} — nothing to answer")

        comments, viewer, voices = self._thread(card)
        self._parked_on(card, comments, number, head)
        holds = _open_holds(voices)
        if holds:
            note = f"; {UNCHECKED_NOTE}" if _unchecked_after_hold(voices) else ""
            raise _Refused(f"re-run: held by {holds[-1]}{note}", "held")
        got = self.run_state(self.repo, ident, comments, viewer,
                             read=self.read, now=self.now)
        why = "; ".join(got.lines)
        if got.state in IN_FLIGHT:
            raise _Refused(f"re-run: a run is in flight or unreadable — {why}",
                           "running")

        if trigger.gate:
            self._waiting(ident, voices, trigger,
                          self._unmet_at_head(pr, number, head), head)

        # A receipt with no readable time is read as the newer, and counted:
        # unread never answers "nothing has been sent" or "budget left".
        receipts = [(_when(v.created_at), r) for v in voices
                    if v.kind == spoken_thread.PIPELINE
                    and (r := proof_run_state.receipt(v.body or "")) is not None]
        newer = [r for when, r in receipts if when is None or when > trigger.at]
        if newer and got.state == "finished":
            self._hold_once(ident, voices, *trigger.unanswered)
            raise _Refused(f"re-run: the run after the proof-run receipt of "
                           f"{newer[-1].at} reads finished, and "
                           f"{trigger.stands} still stands at {head[:7]} — held "
                           f"for an operator: {why}", "held")
        if newer and got.state not in RERUN_AGAIN:
            raise _Refused(f"re-run: {trigger.answer[0]} at {head[:7]} is "
                           f"answered — the proof-run receipt of {newer[-1].at} "
                           f"is newer than {trigger.answer[1]} and its run reads "
                           f"{got.state}: {why}")
        # One budget, whichever reader held the record: every `re-run`
        # receipt since the pull request opened counts (DRE-6488).
        opened = _when(pr.get("createdAt"))
        spent = sum(1 for when, r in receipts
                    if r.count.startswith("re-run")
                    and (opened is None or when is None or when > opened))
        if spent >= RERUN_BUDGET:
            self._hold_once(ident, voices, *trigger.exhausted)
            raise _Refused(f"re-run: budget — {spent} re-runs on #{number} since "
                           f"it opened and {trigger.again}; never a third", "held")
        return (trigger.reason.format(sha7=head[:7]),
                RERUN_COUNT.format(n=spent + 1))

    def _sent_back(self, verdicts: list, number) -> _Trigger:
        """The critic's trigger: its `REQUEST_CHANGES` at the head."""
        sent_back = _when(verdicts[-1].get("createdAt"))
        if sent_back is None:
            raise _Refused(f"re-run: the critic's verdict on #{number} has no "
                           "time to read the receipts against")
        return _Trigger(sent_back, RERUN_REASON, "the critic's REQUEST_CHANGES",
                        ("the findings", "the verdict"),
                        "the critic sent it back again",
                        (RERUN_EXHAUSTED_OBSERVED, RERUN_EXHAUSTED_NEEDS),
                        (RERUN_UNANSWERED_OBSERVED, RERUN_UNANSWERED_NEEDS))

    def _declined(self, pr: dict, number, head: str) -> _Trigger:
        """The gate's trigger (DRE-6488): with the critic's `APPROVE` at the
        head, the gate's newest hold note declines the record AT that head
        as not proven. A note on an earlier head is already answered — the
        gate reads every new head itself — and any other reason is not this."""
        note = _gate_hold_note(pr)
        if note is None:
            raise _Refused(f"re-run: the critic's newest verdict on #{number} at "
                           f"{head[:7]} is APPROVE and the gate has posted no "
                           "decline note on it — nothing to answer")
        line = merge_gate.first_line(note.get("body"))
        found = _DECLINED.search(line)
        reason = found.group("reason") if found else line
        if not reason.startswith(NOT_PROVEN):
            raise _Refused(f"re-run: the gate's newest hold note on #{number} is "
                           f"not a proof-record decline — "
                           f"“{proof_record._cut(reason, 80)}”; nothing to answer")
        sha = found.group("sha")
        if sha != head:
            raise _Refused(f"re-run: the gate's decline on #{number} is at "
                           f"{sha[:7]}, and its head is now {head[:7]} — the "
                           "critic's next verdict and the gate's next reading "
                           "answer the new head; nothing to answer")
        declined = _when(note.get("createdAt"))
        if declined is None:
            raise _Refused(f"re-run: the gate's decline on #{number} has no "
                           "time to read the receipts against")
        return _Trigger(declined, GATE_RERUN_REASON, "the gate's decline",
                        ("the decline", "the gate's note"),
                        "the gate declined it again",
                        (GATE_EXHAUSTED_OBSERVED, GATE_EXHAUSTED_NEEDS),
                        (GATE_UNANSWERED_OBSERVED, GATE_UNANSWERED_NEEDS), gate=True)

    def _unmet_at_head(self, pr: dict, number, head: str) -> list:
        """The record's unmet rows at the head, by the reader the gate held
        on — never the note's quotation. Unread never answers "re-run"."""
        try:
            found = self.read_record(pr, head)
        except Exception as error:  # noqa: BLE001
            found = proof_record.Record(None, None, str(error))
        if found is None or found.text is None:
            detail = getattr(found, "detail", None) or "nothing was read"
            raise _Refused(f"re-run: the record on #{number} could not be read "
                           f"at {head[:7]} — {detail}; nothing dispatched")
        return proof_record.reading(found.text).unmet

    def _waiting(self, ident: str, voices: list, trigger: _Trigger,
                 unmet: list, head: str) -> None:
        """Behavior 1 (DRE-6488): a row waiting on an event holds the card
        instead of a re-run, which could not make the record merge while it
        stands. Posted once per decline: a hold posted after this note and
        since discharged by the operator's `🔬 proof-observed` leaves the
        record to a re-run that re-observes every row."""
        waiting = [(criterion, event) for criterion, result in unmet
                   if (event := proof_record.row_waiting(result))]
        if not waiting:
            return
        observed = "; ".join(proof_record._cut(c, 70) for c, _ in waiting)
        needs = "; ".join(event for _, event in waiting)
        line = linear_ops.proof_waiting_line(observed, needs)
        posted = [_when(v.created_at) for v in voices
                  if _first_line(v.body).startswith(line)]
        if any(when is None or when > trigger.at for when in posted):
            return
        self._hold(ident, observed, needs)
        raise _Refused(f"re-run: {len(waiting)} row(s) of the record at "
                       f"{head[:7]} wait on an event — held for {needs}", "held")

    # -- the dispatch --------------------------------------------------------- #

    def dispatch(self, card: dict, reason: str, count: str) -> None:
        ident = card["identifier"]
        self.tally.eligible += 1
        if not self.live:
            print(f"would: dispatch {ident} — {reason}")
            self.tally.dispatched += 1
            return
        ok, error = self.fire(card, self.repo, reason=reason,
                              event=plan_run.PROOF_EVENT)
        if not ok:
            self.tally.failures.append(error)
            _say(ident, f"ERROR: the dispatch was not confirmed: {error}")
            return
        self.tally.dispatched += 1
        _say(ident, f"dispatched: {reason} ({count})")
        line = (f"{linear_ops.PROOF_MARK} {PROOF_RUN_TAG}: dispatched a proof "
                f"run at {_pt(self.now)} — {reason} ({count})")
        body = pipeline_act.receipt("proof-run-dispatched", line)
        try:
            linear_ops.cmd_comment(ident, body)
        except Exception as error:  # noqa: BLE001
            self.tally.failures.append(f"{ident}: receipt not posted: {error}")
            _say(ident, f"ERROR: dispatched, and the receipt could not be "
                        f"posted: {error}")


def sweep(repo: str, slug: str, *, live: bool, linear=None,
          read: Callable | None = None, find_pr: Callable | None = None,
          run_state: Callable | None = None, release: Callable | None = None,
          fire: Callable | None = None, voices: Callable | None = None,
          now: datetime | None = None,
          find_record: Callable | None = None,
          read_record: Callable | None = None) -> Tally:
    """One pass: the return first, then first runs oldest first and re-runs,
    begun at this pass's turn; at most `PROOF_CANDIDATES_PER_PASS` candidates
    read and one dispatch. A first run held on the lane read is named every
    pass and read by none."""
    one = _Pass(repo, slug, live=live, linear=linear or LinearReads(),
                read=read or github_read,
                find_pr=find_pr or (lambda ident: merged_pr(ident, repo)),
                run_state=run_state or proof_run_state.reading,
                release=release or proof_release.reading,
                fire=fire or plan_run.fire,
                voices=voices or spoken_thread.voices,
                now=now or datetime.now(timezone.utc),
                find_record=find_record or (lambda ident: record_pr(ident, repo)),
                read_record=read_record or (lambda pr, head: record_at(repo, pr, head)))
    tally = one.tally

    # One Green Light read serves both branches; a card the return takes is
    # never also a re-run candidate (DRE-6406).
    green = one.linear.lane(RETURN_LANE)
    returns = [c for c in green if one.is_return(c)]
    returning = {c["identifier"] for c in returns}
    first, held = [], []
    for card in one.linear.lane(FIRST_RUN_LANE):
        if not proof_and_demo.is_proof(card.get("title") or ""):
            continue
        slug_on_card = reconcile.card_repo(card)
        if slug_on_card != slug or (card.get("state") or {}).get("name") != FIRST_RUN_LANE:
            _say(card["identifier"], f"condition 1 (card): its repo: label names "
                                     f"{slug_on_card or 'no repo'}, not {slug}")
            tally.refused += 1
            continue
        why = one.held_on_the_lane(card)
        if why:
            held.append((card, why))
            continue
        first.append(card)
    first.sort(key=lambda c: (_entered(c), _number(c)))
    held.sort(key=lambda pair: (_entered(pair[0]), _number(pair[0])))
    reruns = sorted((c for c in one.linear.lane(RERUN_LANE) + [
                        c for c in green if c["identifier"] not in returning]
                     if one.is_rerun(c)),
                    key=_number)

    # A hold the lane read shows is named every pass and spends no read: the
    # sweep log says why each card waits without it keeping a slot (DRE-6464).
    for card, why in held:
        _say(card["identifier"], f"condition 4 (hold): {why}")
        tally.held += 1

    # The candidates that need a read share one ring, begun at this pass's
    # turn; the return, one per signed answer, keeps the front.
    ring = ([(c, one.first_run) for c in first]
            + [(c, one.rerunning) for c in reruns])
    queue = [(c, one.returning) for c in returns] + _turned(ring, one.now)
    read, tried = 0, False
    for card, decide in queue:
        ident = card["identifier"]
        if tried:
            _say(ident, "deferred — one dispatch per pass, read next pass")
            tally.deferred += 1
            continue
        if read >= PROOF_CANDIDATES_PER_PASS:
            _say(ident, "deferred — candidate cap, read on a later pass")
            tally.deferred += 1
            continue
        read += 1
        try:
            reason, count = decide(card)
        except _Refused as refusal:
            _say(ident, refusal.line)
            setattr(tally, refusal.bucket, getattr(tally, refusal.bucket) + 1)
            continue
        _say(ident, f"eligible: {reason} ({count})")
        # One try per pass, confirmed or not: a refused dispatch is a red
        # run, and the next card waits for a pass that is not failing.
        one.dispatch(card, reason, count)
        tried = True
    print(tally.line(live))
    return tally


def main(argv=None) -> int:
    repo, slug = os.environ.get("REPO"), os.environ.get("REPO_SLUG")
    if not repo or not slug:
        print(f"{PREFIX} REPO and REPO_SLUG must both be set — nothing read",
              file=sys.stderr)
        return 2
    live = is_live()
    try:
        tally = sweep(repo, slug, live=live)
    except reconcile.BoardHeld as held:
        print(f"{PREFIX} the read door holds the board — nothing read, nothing "
              f"dispatched: {held}")
        return 0
    finally:
        print(linear_ops.budget_line())
    for failure in tally.failures:
        print(f"{PREFIX} ERROR: {failure}", file=sys.stderr)
    return 1 if tally.failures else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
