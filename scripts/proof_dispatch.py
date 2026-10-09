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
read only by the return branch. A card in `In Review` has an open record pull
request and is read only by the re-run branch, which moves nothing: the card
stays in `In Review` while its record is amended.

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

## The bound

At most one dispatch per pass — the return first, then first runs oldest
first, then re-runs — and at most `PROOF_CANDIDATES_PER_PASS` candidates read:
two Linear reads for the three lanes (the sweep's board read serves
`Hand-work` and `In Review` together), then at most two per candidate (the
card's epic and relations, and its thread; a re-run reads only its thread).
Three candidates is 2 + 2 × 3 = 8 requests however many proofs wait. Its
`linear-budget:` trailer is its own, lifted into the step summary.

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
import linear_ops  # noqa: E402
import merge_gate  # noqa: E402 — the critic's marker for `standing_verdict`
import pipeline_act  # noqa: E402
import plan_run  # noqa: E402
import proof_and_demo  # noqa: E402
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

#: How many candidates one pass reads, oldest first; the rest wait a pass.
PROOF_CANDIDATES_PER_PASS = 3

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
RERUN_COUNT = "re-run {n} of 2"

#: The re-run budget: per record pull request, apart from the first run's.
RERUN_BUDGET = 2
#: What the critic's newest verdict at the head must say for a re-run.
SENT_BACK = "REQUEST_CHANGES"

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

#: What the sweep's promotion posts as a card lands in `Hand-work` — the one
#: time on the lane read that says when the card entered the lane.
PROMOTED_MARK = f"🧹 Auto-promoted Backlog → {FIRST_RUN_LANE}"

#: The fields the condition-7 lookup needs; `mergeCommit` is the merge's sha.
PR_FIELDS = "number,url,headRefName,state,mergeCommit"
#: The fields the re-run reads off the record pull request: its head, when it
#: opened, and the comments the critic's verdict is read from.
RECORD_FIELDS = "number,url,headRefName,state,headRefOid,createdAt,comments"

#: The card's epic, its siblings and its blocking relations, in one read.
CARD_QUERY = """query($id: String!) { issue(id: $id) {
             %s
             parent { identifier state { name }
               children(first: 100) { nodes {
                 identifier title state { name } labels { nodes { name } } } } }
           } }""" % reconcile.INVERSE_RELATIONS_GQL

_DEAD = re.compile(r"^dead — (run \S+ ended .+? with no record)")


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


def _number(card: dict) -> int:
    digits = (card.get("identifier") or "").rsplit("-", 1)[-1]
    return int(digits) if digits.isdigit() else 0


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
                 release, fire, voices, now, find_record):
        self.repo, self.slug, self.live = repo, slug, live
        self.linear, self.read, self.find_pr = linear, read, find_pr
        self.find_record = find_record
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
        """A PROOF card of this repo in `In Review`, at no request."""
        return (_ours(card, self.slug)
                and (card.get("state") or {}).get("name") == RERUN_LANE)

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
        if standing != SENT_BACK:
            raise _Refused(f"re-run: the critic's newest verdict on #{number} "
                           f"at {head[:7]} is {standing} — nothing to answer")
        sent_back = _when(verdicts[-1].get("createdAt"))
        if sent_back is None:
            raise _Refused(f"re-run: the critic's verdict on #{number} has no "
                           "time to read the receipts against")

        comments, viewer, voices = self._thread(card)
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

        # A receipt with no readable time is read as the newer, and counted:
        # unread never answers "nothing has been sent" or "budget left".
        receipts = [(_when(v.created_at), r) for v in voices
                    if v.kind == spoken_thread.PIPELINE
                    and (r := proof_run_state.receipt(v.body or "")) is not None]
        newer = [r for when, r in receipts if when is None or when > sent_back]
        if newer and got.state == "finished":
            self._hold_once(ident, voices, RERUN_UNANSWERED_OBSERVED,
                            RERUN_UNANSWERED_NEEDS)
            raise _Refused(f"re-run: the run after the proof-run receipt of "
                           f"{newer[-1].at} reads finished, and the critic's "
                           f"REQUEST_CHANGES still stands at {head[:7]} — held "
                           f"for an operator: {why}", "held")
        if newer and got.state not in RERUN_AGAIN:
            raise _Refused(f"re-run: the findings at {head[:7]} are answered — "
                           f"the proof-run receipt of {newer[-1].at} is newer "
                           f"than the verdict and its run reads {got.state}: "
                           f"{why}")
        opened = _when(pr.get("createdAt"))
        spent = sum(1 for when, r in receipts
                    if r.count.startswith("re-run")
                    and (opened is None or when is None or when > opened))
        if spent >= RERUN_BUDGET:
            self._hold_once(ident, voices, RERUN_EXHAUSTED_OBSERVED,
                            RERUN_EXHAUSTED_NEEDS)
            raise _Refused(f"re-run: budget — {spent} re-runs on #{number} since "
                           "it opened and the critic sent it back again; never "
                           "a third", "held")
        return (RERUN_REASON.format(sha7=head[:7]),
                RERUN_COUNT.format(n=spent + 1))

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
          find_record: Callable | None = None) -> Tally:
    """One pass: the return first, then first runs oldest first, then
    re-runs; at most `PROOF_CANDIDATES_PER_PASS` candidates read and one
    dispatch."""
    one = _Pass(repo, slug, live=live, linear=linear or LinearReads(),
                read=read or github_read,
                find_pr=find_pr or (lambda ident: merged_pr(ident, repo)),
                run_state=run_state or proof_run_state.reading,
                release=release or proof_release.reading,
                fire=fire or plan_run.fire,
                voices=voices or spoken_thread.voices,
                now=now or datetime.now(timezone.utc),
                find_record=find_record or (lambda ident: record_pr(ident, repo)))
    tally = one.tally

    returns = [c for c in one.linear.lane(RETURN_LANE) if one.is_return(c)]
    first = []
    for card in one.linear.lane(FIRST_RUN_LANE):
        if not proof_and_demo.is_proof(card.get("title") or ""):
            continue
        slug_on_card = reconcile.card_repo(card)
        if slug_on_card != slug or (card.get("state") or {}).get("name") != FIRST_RUN_LANE:
            _say(card["identifier"], f"condition 1 (card): its repo: label names "
                                     f"{slug_on_card or 'no repo'}, not {slug}")
            tally.refused += 1
            continue
        first.append(card)
    first.sort(key=lambda c: (_entered(c), _number(c)))
    reruns = sorted((c for c in one.linear.lane(RERUN_LANE) if one.is_rerun(c)),
                    key=_number)

    queue = ([(c, one.returning) for c in returns]
             + [(c, one.first_run) for c in first]
             + [(c, one.rerunning) for c in reruns])
    read, tried = 0, False
    for card, decide in queue:
        ident = card["identifier"]
        if tried:
            _say(ident, "deferred — one dispatch per pass, read next pass")
            tally.deferred += 1
            continue
        if read >= PROOF_CANDIDATES_PER_PASS:
            _say(ident, "deferred — candidate cap, read next pass")
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
