#!/usr/bin/env python3
"""A comment the CEO leaves on a Green Light plan is picked up (DRE-6138).

When the CEO writes on a plan waiting for him in `Green Light` — a question,
a correction, "change X and then I'll approve" — nothing read it: the
console's Answer press moves the card, and a comment that moves nothing
waited for an operator to notice. This phase notices. It finds the newest
comment in his voice nothing has answered yet and sends the plan back for a
review, once per comment: the planner changes the plan the way he asked or
answers him in plain English, and both critics read it again before it
returns to Green Light. One receipt line on the card says his comment was
heard.

Its own module and its own step in `reconcile.yml` (after `Dispatch proof
runs`, full passes only), because `reconcile.py` is past twelve thousand
lines and an edit to a file that size is what killed DRE-3088 three times.

## What qualifies — every condition, read in order

  1. An epic by the sweep's own test (`reconcile.card_is_epic`), not a
     `PROOF:` card (`proof_dispatch` owns those), not `epic-queued` (approved
     and waiting at the cap — a review would take it out of line), and its
     `repo:` label names this repo. All four off the lane read, at no request.
  2. Its newest comment in THE CEO'S VOICE is a `ceo-via-console` answer
     (`spoken_thread.voices` checked the signature), or a `person` comment
     whose Linear user id is declared in `config/green-light-reply.json`.
     Nothing else counts — `pipeline`, `integration`, unknown, refused and
     could-not-be-checked all fail closed. A pipeline comment after his does
     not hide it.
  3. Nothing answered it: no `💬 green-light-reply` receipt names its
     `createdAt` stamp, and no planning cycle started after it
     (`plan_critic.cycle_marker`, read through `current_cycle_entries` so a
     quoted copy opens nothing).

## Who said it

`spoken_thread.voices` returns one `Voice` per node, in the same order, so
the two lists are paired by position: the voice off `voices[i]`, the Linear
user id off `nodes[i]`. The label text is never read, and the user id of a
`ceo-via-console` comment is never read at all — that voice is the
signature's, not the poster's.

## The dispatch

`plan_run.fire` with the payload `review_rerun.py dispatch` builds, and the
lane word the card is in: `trigger_state="green light"` gets past the
duplicate guard (`dedupe_dispatch.lane_left_behind`) and the route table
reads it with `re-run` as review mode. `review_rerun.py dispatch` is not
called: its `--trigger-state` takes `planning` and `in progress` only, and
neither is right for a card in Green Light. The receipt goes up only on a
confirmed dispatch (DRE-2034), and it is not a registered act — the
registry's `unconverted` block names it, until the console's half exists
(DRE-3091).

## The bound

At most one dispatch per pass and at most `GREEN_LIGHT_REPLY_CANDIDATES_PER_PASS`
whole-thread reads (DRE-5850), newest unanswered comment first. A card whose
lane-read window holds no comment that could be his, or only ones a receipt
already names, costs no read; a window Linear says is partial is always read.
Its `linear-budget:` trailer is printed on every exit.

## The dry run

Unless `GREEN_LIGHT_REPLY_LIVE` is exactly `true`, the phase prints a
`would:` line per qualifying card it read and writes nothing.

CLI (the step's own call; reads `REPO`, `REPO_SLUG`, `GREEN_LIGHT_REPLY_LIVE`):

    green_light_reply.py
"""

from __future__ import annotations

import json
import os
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import console_receipt  # noqa: E402
import epic_cap  # noqa: E402
import linear_ops  # noqa: E402
import plan_critic  # noqa: E402
import plan_run  # noqa: E402
import proof_and_demo  # noqa: E402
import review_rerun  # noqa: E402
import spoken_thread  # noqa: E402

# `reconcile` is imported where it is used: it reads `REPO` at import, and a
# missing one must be answered by `main`, not by a KeyError.

#: Opens every line this phase prints.
PREFIX = "green-light-reply:"

LANE = "Green Light"
#: The lane the card is in, lower-cased, as the relay sends it.
TRIGGER_STATE_GREEN_LIGHT = "green light"

#: How many threads one pass reads; the rest wait a pass.
GREEN_LIGHT_REPLY_CANDIDATES_PER_PASS = 3

#: The repository variable that turns the dry run off — `true` and nothing else.
LIVE_VARIABLE = "GREEN_LIGHT_REPLY_LIVE"

#: The CEO's Linear user ids, as data. The operator declares them (DRE-6498).
CONFIG = Path(__file__).resolve().parent.parent / "config" / "green-light-reply.json"

#: The receipt's opener, and what this phase reads back off it.
RECEIPT_OPENER = "💬 green-light-reply:"
_NAMED = re.compile(r"\bcomment=(\S+)")

#: The two voices that are his.
_HIS = (spoken_thread.CEO_VIA_CONSOLE, spoken_thread.PERSON)


def is_live() -> bool:
    return os.environ.get(LIVE_VARIABLE) == "true"


def ceo_user_ids(path: Path = CONFIG) -> frozenset:
    """The declared Linear user ids of the CEO. A file that does not say a
    list of ids raises — it is never read as an empty list."""
    doc = json.loads(Path(path).read_text(encoding="utf-8"))
    ids = doc.get("ceo_linear_user_ids") if isinstance(doc, dict) else None
    if not isinstance(ids, list) or not all(isinstance(i, str) for i in ids):
        raise ValueError(f"{path}: `ceo_linear_user_ids` must be a list of "
                         "Linear user ids")
    return frozenset(i.strip() for i in ids if i.strip())


def reply_receipt(stamp: str, voice: str) -> str:
    """The one line posted when his comment was sent back for a review."""
    return (f"{RECEIPT_OPENER} comment={stamp} voice={voice} "
            f"at={spoken_thread.pacific_label(stamp)} → review re-run")


def _named(body: str | None) -> str | None:
    """The stamp a receipt names, or None when `body` is not one."""
    text = (body or "").lstrip()
    if not text.startswith(RECEIPT_OPENER):
        return None
    found = _NAMED.search(text.split("\n", 1)[0])
    return found.group(1) if found else None


def _author(node: dict) -> str | None:
    return (node.get("user") or {}).get("id")


# --------------------------------------------------------------------------- #
# The reads — injected in the tests, these in production                       #
# --------------------------------------------------------------------------- #


class LinearReads:
    """The two Linear reads this phase makes, and nothing else."""

    def lane(self, state: str) -> list:
        """The lane through the sweep's own read."""
        import reconcile  # noqa: PLC0415 — see the import note above
        return reconcile.active_cards((state,))

    def thread(self, identifier: str):
        """The whole thread and the viewer, paged past the window (DRE-5850)."""
        return linear_ops._thread_and_viewer(identifier, "body", "user",
                                             "createdAt", whole=True)


# --------------------------------------------------------------------------- #
# The pass                                                                     #
# --------------------------------------------------------------------------- #


@dataclass
class Tally:
    lane: int = 0
    candidates: int = 0
    read: int = 0
    qualifying: int = 0
    dispatched: int = 0
    answered: int = 0
    not_his: int = 0
    unchecked: int = 0
    unreadable: int = 0
    deferred: int = 0
    failures: list = field(default_factory=list)

    def line(self, live: bool) -> str:
        return (f"{PREFIX} green light {self.lane}, candidates "
                f"{self.candidates}, read {self.read}, qualifying "
                f"{self.qualifying}, dispatched {self.dispatched}, answered "
                f"{self.answered}, no comment of his {self.not_his}, "
                f"unchecked {self.unchecked}, unreadable {self.unreadable}, "
                f"deferred {self.deferred} "
                f"({'live' if live else 'dry run'})")


class _Skip(Exception):
    """No dispatch: `bucket` is the tally it counts in, `loud` sends the line
    to stderr."""

    def __init__(self, line: str, bucket: str, loud: bool = False):
        super().__init__(line)
        self.line, self.bucket, self.loud = line, bucket, loud


def _say(identifier: str, text: str, *, loud: bool = False) -> None:
    print(f"{PREFIX} {identifier} — {text}", file=sys.stderr if loud else sys.stdout)


def _labels(card: dict) -> list:
    return [(lbl.get("name") or "").lower()
            for lbl in (card.get("labels") or {}).get("nodes") or []]


def _candidate(card: dict, slug: str) -> bool:
    """Condition 1, off the lane read alone."""
    import reconcile  # noqa: PLC0415 — see the import note above
    return (reconcile.card_repo(card) == slug
            and not proof_and_demo.is_proof(card.get("title") or "")
            and epic_cap.QUEUED_LABEL not in _labels(card)
            and reconcile.card_is_epic(card))


def _owed(card: dict, ids: frozenset) -> str | None:
    """The sort key that spends a read on `card`, or None when its window
    rules him out: the newest stamp of a comment that could be his — an
    answer-shaped trailer, or a declared author — that no receipt in the
    window names. A partial window always spends one, sorted last (`""`).

    A superset of what the thread read decides, never a decision: the
    trailer is not checked here, and nothing here counts a comment."""
    window = linear_ops.window_nodes(card.get("comments"))
    named = {_named(c.get("body")) for c in window} - {None}
    owed = [c.get("createdAt") or "" for c in window
            if (console_receipt.has_answer_trailer(c.get("body"))
                or _author(c) in ids)
            and (c.get("createdAt") or "") not in named]
    if owed:
        return max(owed)
    return "" if linear_ops.window_is_partial(card.get("comments")) else None


class _Pass:
    def __init__(self, repo, slug, *, live, linear, fire, voices, ids):
        self.repo, self.slug, self.live = repo, slug, live
        self.linear, self.fire, self.voices, self.ids = linear, fire, voices, ids
        self.tally = Tally()

    def decide(self, card: dict) -> tuple:
        """`(stamp, voice)` of his newest unanswered comment, or `_Skip`."""
        ident = card["identifier"]
        try:
            nodes, viewer = self.linear.thread(ident)
            voices = self.voices(nodes, viewer, card=ident)
        except Exception as error:  # noqa: BLE001 — unread is never "no comment"
            raise _Skip(f"skipped: the thread could not be read: {error}",
                        "unreadable", loud=True)
        if len(voices) != len(nodes):
            raise _Skip(f"skipped: the reader labeled {len(voices)} of "
                        f"{len(nodes)} comments, so none can be paired",
                        "unreadable", loud=True)

        his = None
        for i, (node, voice) in enumerate(zip(nodes, voices)):
            if voice.kind == spoken_thread.CEO_VIA_CONSOLE or (
                    voice.kind == spoken_thread.PERSON and _author(node) in self.ids):
                his = i
        # A console answer the check could not RUN on is neither his voice nor
        # a refused one (DRE-4153). After his newest comment it may be his
        # newest word, so nothing is sent back until a pass that can check it.
        unchecked = [v for v in voices[(-1 if his is None else his) + 1:]
                     if v.kind == spoken_thread.UNCHECKED]
        if unchecked:
            raise _Skip(f"not dispatched: an unchecked console answer posted "
                        f"{spoken_thread.pacific_label(unchecked[-1].created_at)} "
                        "COULD NOT BE CHECKED (the console's key could not be "
                        "read) — it counts for nothing until a pass that can "
                        "check it", "unchecked", loud=True)
        if his is None:
            raise _Skip(self._not_his(nodes, voices), "not_his", loud=True)

        stamp, voice = nodes[his].get("createdAt"), voices[his].kind
        if not stamp:
            raise _Skip(f"not dispatched: his newest comment ({voice}) has no "
                        "createdAt to name it by", "not_his", loud=True)
        # Anyone's receipt answers it: a copy can only keep a plan from being
        # sent back, never send one, and with no viewer the pipeline's own
        # receipt reads `unknown`.
        if any(_named(v.body) == stamp for v in voices):
            raise _Skip(f"answered: a receipt names his comment {stamp}",
                        "answered")
        after = [{"body": v.body or "", "created_at": v.created_at,
                  "authored_by_pipeline": v.kind == spoken_thread.PIPELINE}
                 for v in voices[his + 1:]]
        trusted = [e for e in after if e["authored_by_pipeline"]]
        if len(plan_critic.current_cycle_entries(after, ident)) < len(trusted):
            raise _Skip(f"answered: a planning cycle started after his comment "
                        f"{stamp}", "answered")
        return stamp, voice

    def _not_his(self, nodes: list, voices: list) -> str:
        """The one line for a thread with no comment in his voice: the newest
        comment that does not count, a person's or another's first."""
        rest = [(n, v) for n, v in zip(nodes, voices)
                if v.kind != spoken_thread.PIPELINE]
        if not (rest or voices):
            return "not dispatched: the thread holds no comment"
        node, voice = (rest or list(zip(nodes, voices)))[-1]
        why = (" from a Linear user not declared as the CEO's"
               if voice.kind == spoken_thread.PERSON else "")
        return (f"not dispatched: no comment in the CEO's voice — the newest "
                f"that does not count is {voice.kind}{why}, "
                f"{spoken_thread.pacific_label(node.get('createdAt'))}")

    def dispatch(self, card: dict, stamp: str, voice: str, *, first: bool) -> bool:
        """True when the pass is spent: one dispatch tried, confirmed or not."""
        ident = card["identifier"]
        self.tally.qualifying += 1
        if not self.live:
            later = "" if first else " on a later pass — one dispatch per pass"
            print(f"would: dispatch {ident}{later} — a review re-run for the "
                  f"CEO's comment {stamp} ({voice}), then post: "
                  f"{reply_receipt(stamp, voice)}")
            return False
        ok, error = self.fire(card, self.repo,
                              trigger_state=TRIGGER_STATE_GREEN_LIGHT,
                              reason=review_rerun.REASON_RERUN_ACT,
                              event=plan_run.PLAN_EVENT)
        if not ok:
            self.tally.failures.append(f"{ident}: the dispatch was not "
                                       f"confirmed: {error}")
            _say(ident, f"ERROR: the dispatch was not confirmed, no receipt "
                        f"posted: {error}", loud=True)
            return True
        self.tally.dispatched += 1
        _say(ident, f"dispatched: a review re-run for the CEO's comment {stamp} "
                    f"({voice})")
        try:
            refused = linear_ops.cmd_comment(ident, reply_receipt(stamp, voice))
        except Exception as error:  # noqa: BLE001
            refused = str(error)
        if refused:
            self.tally.failures.append(f"{ident}: dispatched, and the receipt "
                                       f"could not be posted: {refused}")
            _say(ident, f"ERROR: dispatched, and the receipt could not be "
                        f"posted: {refused}", loud=True)
        return True


def sweep(repo: str, slug: str, *, live: bool, linear=None,
          fire: Callable | None = None, voices: Callable | None = None,
          ceo_ids: frozenset | None = None) -> Tally:
    """One pass: candidates off the lane read, newest unanswered comment
    first; at most `GREEN_LIGHT_REPLY_CANDIDATES_PER_PASS` threads read and
    one dispatch."""
    one = _Pass(repo, slug, live=live, linear=linear or LinearReads(),
                fire=fire or plan_run.fire,
                voices=voices or spoken_thread.voices,
                ids=ceo_user_ids() if ceo_ids is None else ceo_ids)
    tally = one.tally
    cards = one.linear.lane(LANE)
    tally.lane = len(cards)
    queue = []
    for card in cards:
        if not _candidate(card, slug):
            continue
        tally.candidates += 1
        key = _owed(card, one.ids)
        if key is not None:
            queue.append((key, card))
    queue.sort(key=lambda pair: pair[0], reverse=True)

    spent = False
    for _, card in queue:
        ident = card["identifier"]
        if spent:
            _say(ident, "deferred — one dispatch per pass, read next pass")
            tally.deferred += 1
            continue
        if tally.read >= GREEN_LIGHT_REPLY_CANDIDATES_PER_PASS:
            _say(ident, "deferred — thread-read cap, read next pass")
            tally.deferred += 1
            continue
        tally.read += 1
        try:
            stamp, voice = one.decide(card)
        except _Skip as skip:
            _say(ident, skip.line, loud=skip.loud)
            setattr(tally, skip.bucket, getattr(tally, skip.bucket) + 1)
            continue
        _say(ident, f"qualifies: the CEO's comment {stamp} ({voice}) is unanswered")
        spent = one.dispatch(card, stamp, voice,
                             first=not tally.qualifying)
    print(tally.line(live))
    return tally


def main(argv=None) -> int:
    try:
        return _main()
    finally:
        print(linear_ops.budget_line())


def _main() -> int:
    repo, slug = os.environ.get("REPO"), os.environ.get("REPO_SLUG")
    if not repo or not slug:
        print(f"{PREFIX} REPO and REPO_SLUG must both be set — nothing read",
              file=sys.stderr)
        return 2
    import reconcile  # noqa: PLC0415 — see the import note above
    live = is_live()
    try:
        tally = sweep(repo, slug, live=live)
    except reconcile.BoardHeld as held:
        print(f"{PREFIX} the read door holds the board — nothing read, nothing "
              f"dispatched: {held}")
        return 0
    for failure in tally.failures:
        print(f"{PREFIX} ERROR: {failure}", file=sys.stderr)
    return 1 if tally.failures else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
