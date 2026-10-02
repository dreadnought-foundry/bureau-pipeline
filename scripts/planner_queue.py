#!/usr/bin/env python3
"""The fleet-wide planner slot ledger, kept on Linear (DRE-5176, epic DRE-5167).

Every planner run in every repo shares ONE Linear key, and on 2026-09-28
nineteen `agent-plan` runs released in one minute drained it. The CEO's answer
the same day was a cap: at most four planners running across the fleet. An
App installation token is scoped to one owner and the roster spans three, so
no run can count the fleet's Actions runs with its own token. Linear is the
one store every planner run already holds the key to, so the ledger lives
THERE, as receipts on the cards themselves:

    🎟️ planner-slot: <state> · card <DRE-N> · run <run id> · repo <owner/name> · trigger <lane> · at <ISO-8601 UTC>

`<state>` is `claimed`, `waiting`, `dispatched` or `released`. A `waiting`
receipt carries a second line, `waiting for a planner: place <k> of <n>`. A
`claimed` receipt may carry `· from run <sender>` (the handover), a `waiting`
or `dispatched` one `· reason <word>` (the `client_payload.reason` the run
arrived with); both sit after `trigger` and before `at`. A `released` receipt
may end `· because <finished|expired|run-gone|duplicate|parked>`.

This module owns the grammar, the pure decision logic and the four writes.
Nothing here touches a workflow: the `plan.yml` and `reconcile.py` cards of the
epic call what this one declares.

THE CAP IS ONE NUMBER. `config/planner-queue.json` holds it, found beside this
script the way `lane_contract.CONTRACT_PATH` finds its own file — so a product
repo running `.bureau-pipeline/scripts/planner_queue.py` reads
`.bureau-pipeline/config/planner-queue.json`, never the product's `config/`.
`PLANNER_QUEUE_CONFIG` overrides it (the test seam). A missing or malformed
file RAISES; nothing falls back to a number nobody chose. `cap()` is the one
reader of `max_running` and `waiting_max()` the one reader of
`waiting_max_minutes`.

THE NUMBER IS THREE (DRE-5634, 2026-10-02). It shipped as four, and DRE-5326
dropped it to two: on 2026-09-30 a groom drain put nineteen cards into
Planning at 07:05 PT, four planner and critic steps ran at once, and four
together spent about 185-260 Linear requests a minute against a key that
refills about 42 a minute (2,500 an hour). The key was at zero by 07:40 PT
and every Linear-touching run in the fleet was refused. Two running spent
about 27 a minute. Since 2026-10-02 09:37 PT the planners spend their own
5,000-an-hour OAuth bucket instead of that key (DRE-5589, the token renewed
by the console, DRE-5587), so planners no longer starve the sweeps, and the
CEO raised the number to three. The groom drain reads this ledger too, and
releases no more cards than there are free slots (`groomer.free_planner_slots`).

THE RULES, in the order the ledger applies them.

  * A `released` receipt closes the claim of the run it names and no other,
    and a `claimed … · from run <R>` closes R's claim the same way. So does
    a run's own later receipt — a refused run's `waiting` ends its claim. A
    release that closes nothing (a run that held no open claim) and a
    `because duplicate` release are about a RUN, not the card: neither moves
    the card's state or its place in line.
  * A `because parked` release is about the CARD though it closes nothing
    (DRE-5378). The sweep posts it when the watchdog parks a waiting card
    out of Planning: the card leaves the line, and a re-send starts a fresh
    wait. Without it DRE-5213, parked at 13:40 PT on 2026-09-30 and re-sent
    at 14:00, came back 411 minutes "waited" and was parked again at 14:26.
  * A card's OPEN CLAIM is its newest `claimed` receipt inside
    `claim_ttl_minutes` that no later receipt closes. A claim older than the
    TTL counts as released — the TTL is at least `plan.yml`'s
    `jobs.plan.timeout-minutes`, because a job that hits its timeout is
    canceled and skips its `always()` release, and the only thing that can
    end that claim without a GitHub read is that no plan job runs longer.
  * A card's STATE is `claimed` while it holds an open claim, and otherwise
    its newest receipt — a `dispatched` older than `dispatched_grace_minutes`
    reads as `waiting` again (the dispatch was lost).
  * A card's LINE ENTRY is the `createdAt` of its earliest `waiting` receipt
    newer than its newest release. Nothing it posts later moves it: a card
    dispatched from the line that finds its slot taken waits again at the
    same place, never at the back. The wait is measured from the entry.
  * A RE-RUN KEEPS ITS PLACE (DRE-5378). A card's PLACE, what orders the
    line, is its line entry — unless the first run to claim since its newest
    release is the run that posted that release. Only a GitHub re-run can
    claim under a run id that has already released, because a re-run keeps
    its run id; its place is then the place the claim it continues had,
    and that claim's `createdAt` when it had none. On 2026-09-30 DRE-5213's
    run 36726491495 claimed at 14:05:58Z, released at 14:34:52Z `because
    finished`, and its automatic retry claimed under the same id at 14:35:55Z
    and waited at place 24 of 24 — behind every card of the morning's groom
    drain, each of which had claimed after 14:05:58Z. It waited six hours.
    Its wait is still measured from its own `waiting`: it held a slot until
    14:34:52Z and was not waiting then. A different run after a release — a
    re-send, a new dispatch — is a fresh arrival and joins at the back.
  * ADMISSION IS BY CLAIM ORDER. `claim` posts the card's `claimed` receipt
    FIRST, then reads. A slot is taken by every other card's open claim with
    an earlier `(createdAt, comment id)` and by every other card's in-grace
    `dispatched` receipt. Under the cap the run is admitted; otherwise it
    posts `waiting` with its place. A later claimant counts the earlier
    claim, and the earlier never counts the later — that is what bounds the
    fleet at the cap under every interleaving. Nothing revokes an admitted
    claim but its own release, the TTL, or the sweep's run-gone check.
  * THE HANDOVER. A planner run's own re-dispatch (`re-review`,
    `review-retry`) carries the sender's run id as `sent_by_run`. When that
    run holds the card's open claim, the child posts `claimed … · from run
    <S>` and is admitted with no ledger read: the slot passes to the run it
    asked for, keeping the sender's place in claim order. When S's claim is
    already closed, the child claims as any run does.
  * Two open claims on one card are ONE entry, keyed by the earlier; the
    later run posts `released … because duplicate` for itself and no
    `waiting`.
  * The line: a `waiting` receipt whose trigger is `in progress` (the activate
    route, a CEO approval) sorts to the FRONT; otherwise first come, first
    served by place.

THE LANES. The ledger reads `LEDGER_LANES`: a plan-route card sits in
`Planning` (where it stays when the plan route hands it to the second critic),
an activate-route epic sits `In Progress`, and `Green Light` is where the
review route moves an epic once both critics have passed it and where the
second critic's send-back and the escalation exit put a card BEFORE the run
holding its claim reaches its release — so a run's slot must still be counted
after that move. A Green Light card is read for its open claim only: a
`waiting` receipt there belongs to a card a person moved there, and nothing dispatches a planner at a card in the
CEO's queue. Two lanes are still outside the read, and the window is stated
rather than hidden: the one-off route moves its card to `Backlog` and the
classification bounce moves a card to `Triage` a few steps before the
release; in that window the run holds a slot the ledger cannot see, and no
model step runs in it (the death receipt and the release are all that
follow). The stall watchdog's park is a second `Triage` window (DRE-5286): it
moves a stalled card there, and since a card parked out of the line has its
place released first, what it can leave behind is an open `claimed` receipt —
a claim whose run went silent — which stops being counted once the card
leaves `LEDGER_LANES`. A card whose comment window holds no
planner-slot receipt is neither running nor waiting — the fail-open
direction: a run is admitted rather than a card dropped.

THE LINEAR BOUNDARY — the five vendor-boundary questions
(standards/vendor-boundaries.md):

  Q1 (whose identity, whose quota). Every read and write here goes through
     `linear_ops` with the key the calling job holds: a planner run's calls
     draw on the ONE planner key every planner run shares — the quota this
     cap protects — and the sweep's calls on the sweep's own key. An admitted
     run spends about five requests (the claim write is a card read plus a
     mutation, the ledger read one request per hundred cards in the three
     lanes, the release another write); a refused run two more for its
     `waiting`. `dispatch` fires the `repository_dispatch` with the App token
     in `GH_TOKEN`, exactly as `review_rerun.py dispatch` does, and the run
     it starts initiates as that App.
  Q2 (which secrets store). None of its own: the Linear key comes from the
     job's environment through `linear_ops.api_key`, and a run with no key
     fails at the first write — which `claim` turns into an admission and a
     `::warning::`, never a refused planner.
  Q3 (what the vendor does on retry). `linear_ops.gql` retries a transient
     5xx once, so a write can land twice. Every receipt is safe doubled: a
     second `claimed` from the same run is the same run's claim, a second
     `waiting` leaves the line entry at the earlier one, a second `released`
     closes nothing new. Nothing here edits or deletes a comment; a receipt
     a person deletes is simply not read, and a card with no receipt is
     admitted rather than dropped. A card at Linear's comment cap makes the
     write report the condition, and `claim` fails open on it. Anyone with
     comment access can post a receipt-shaped line; the worst a forged
     `claimed` buys is one slot until the TTL.
  Q4 (the command's limits). The ledger read sees the newest
     `linear_ops.COMMENT_WINDOW` comments of each card, newest first, a
     hundred cards a page through `gql_paged`. A receipt older than the
     window is invisible, which reads as no receipt — the fail-open
     direction. `createdAt` is Linear's clock and ages are measured against
     the runner's: seconds of skew against minute-scale bounds.
  Q5 (a crash mid-flow). A crash between the claim write and the ledger read
     leaves an open `claimed` receipt that every later claimant counts as
     running, until the job's release step, the TTL, or the sweep's run-gone
     check ends it — a crash can make the fleet run fewer planners, never
     more. A crash after the read and before the `waiting` write leaves the
     same open claim, and after it ends the card is in no line; the Planning
     stall watch (`flag_stalled_planning`) is what finds that card. A failed
     `dispatch` posts no `dispatched` receipt, so the card stays waiting.

BESIDE THE EPIC CAP (DRE-5129), which lands after this epic. DRE-5152's
relay-missed confirmation reads "a pipeline-authored comment newer than the
`epic-started` receipt" as evidence the activate run was reached, and a
`claimed` or `waiting` planner-slot receipt is exactly such a comment: the run
reached the slot step, and a waiting epic is served by the planner line, not by
a second dispatch. That reading holds only because this epic lands first — by
the time DRE-5152's confirmation runs, every activate run posts a planner-slot
receipt before anything else. Neither card is changed by the other.

EVERY LINEAR WRITE of the epic goes through this module's one `_post`, the only
caller of `linear_ops.cmd_comment` here. It is declared in
`config/pipeline-acts.json`'s `unconverted` block: the receipt carries its own
single-definition grammar, and it is not a new act.

CLI (every command but `check` exits 0 on every path):
  check                     exit 0 on a valid config file; 2 with the reason
  claim <CARD> --run-id ID --repo O/N --trigger-state LANE
        [--sent-by-run ID] [--reason WORD] [--github-output PATH]
                            admitted= place= waiting= duplicate= inherited=
  release <CARD> --run-id ID [--because REASON]
  next [--github-output PATH]
                            card= repo= owner= name= trigger_state= reason=
  dispatch <CARD> --run-id ID --repo O/N --trigger-state LANE [--reason WORD]
"""

from __future__ import annotations

import argparse
import functools
import json
import os
import re
import sys
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

SLOT_MARK = "🎟️"
SLOT_TAG = "planner-slot"
LEDGER_LANES = ("Planning", "In Progress", "Green Light")
#: The lanes a card can WAIT in. A Green Light card is read for its open
#: claim only — it is in the CEO's queue, not in line.
WAITING_LANES = ("Planning", "In Progress")
STATES = ("claimed", "waiting", "dispatched", "released")
BECAUSE = ("finished", "expired", "run-gone", "duplicate", "parked")
#: The trigger that sorts to the front of the line: the activate route.
FRONT_TRIGGER = "in progress"
PLACE_LINE = "waiting for a planner: place {place} of {of}"

CONFIG_ENV = "PLANNER_QUEUE_CONFIG"
_HERE = os.path.dirname(os.path.abspath(__file__))
CONFIG_PATH = os.path.normpath(os.path.join(_HERE, "..", "config", "planner-queue.json"))
CONFIG_KEYS = ("max_running", "claim_ttl_minutes", "dispatched_grace_minutes",
               "waiting_max_minutes")

#: One card's comments, for the handover check — the same fields the ledger
#: read selects, for one card.
CARD_COMMENTS_QUERY = """query($id: String!) { issue(id: $id) {
     identifier updatedAt state { name }
     comments(first: %d) { nodes { id body createdAt } } } }"""


class PlannerQueueError(RuntimeError):
    """The queue could not do what it was asked: a bad config file (named, with
    its defect), or a receipt that did not land."""


# --------------------------------------------------------------------------- #
# the config                                                                   #
# --------------------------------------------------------------------------- #


def config_path(path: str | None = None) -> str:
    return path or os.environ.get(CONFIG_ENV) or CONFIG_PATH


def load(path: str | None = None) -> dict:
    """The four numbers, validated. Raises `PlannerQueueError` naming the
    resolved path and the defect — never a default."""
    where = config_path(path)
    try:
        with open(where, encoding="utf-8") as fh:
            doc = json.load(fh)
    except OSError as exc:
        raise PlannerQueueError(
            f"{where}: cannot read the planner queue config ({exc.strerror or exc})"
        ) from exc
    except ValueError as exc:
        raise PlannerQueueError(f"{where}: not valid JSON ({exc})") from exc
    if not isinstance(doc, dict):
        raise PlannerQueueError(f"{where}: expected a JSON object")
    out = {}
    for key in CONFIG_KEYS:
        if key not in doc:
            raise PlannerQueueError(f"{where}: missing key {key!r}")
        value = doc[key]
        if isinstance(value, bool) or not isinstance(value, int):
            raise PlannerQueueError(f"{where}: {key} must be an integer, got {value!r}")
        if value <= 0:
            raise PlannerQueueError(f"{where}: {key} must be positive, got {value}")
        out[key] = value
    return out


def cap() -> int:
    return load()["max_running"]


def waiting_max() -> int:
    return load()["waiting_max_minutes"]


# --------------------------------------------------------------------------- #
# the grammar                                                                  #
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class Receipt:
    state: str
    card: str
    run: str
    repo: str
    trigger: str
    at: str
    from_run: str | None = None
    reason: str | None = None
    because: str | None = None
    created_at: str = ""
    comment_id: str = ""
    place: int | None = None
    of: int | None = None


_HEAD = re.compile(r"^\U0001f39f️?\s*" + re.escape(SLOT_TAG) + r":\s*(\S+)$")
_PLACE = re.compile(r"^waiting for a planner: place (\d+) of (\d+)$")
# "from run" before "run" only for clarity: a part is matched on its prefix.
_FIELDS = ("from run", "card", "run", "repo", "trigger", "reason", "at", "because")
_REQUIRED = ("card", "run", "repo", "trigger", "at")


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _iso(at: datetime) -> str:
    return at.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _dt(value) -> datetime | None:
    if value is None or value == "":
        return None
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    return _parse_time(str(value))


@functools.lru_cache(maxsize=8192)
def _parse_time(text: str) -> datetime | None:
    try:
        parsed = datetime.fromisoformat(text.strip().replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def _field(value) -> str:
    """One receipt field: one line, no separator inside it."""
    text = " ".join(str(value).replace("·", " ").split())
    return text or "-"


def format_receipt(state: str, *, card: str, run: str, repo: str, trigger: str,
                   at: str, from_run: str | None = None, reason: str | None = None,
                   because: str | None = None, place: int | None = None,
                   of: int | None = None) -> str:
    """The one place a receipt's text is composed."""
    if state not in STATES:
        raise PlannerQueueError(f"unknown planner-slot state {state!r}")
    parts = [f"{SLOT_MARK} {SLOT_TAG}: {state}", f"card {_field(card)}",
             f"run {_field(run)}", f"repo {_field(repo)}", f"trigger {_field(trigger)}"]
    if from_run:
        parts.append(f"from run {_field(from_run)}")
    if reason:
        parts.append(f"reason {_field(reason)}")
    parts.append(f"at {_field(at)}")
    if because:
        parts.append(f"because {_field(because)}")
    body = " · ".join(parts)
    if state == "waiting":
        body += "\n" + PLACE_LINE.format(place=place or 1, of=of or place or 1)
    return body


def parse_receipt(body) -> Receipt | None:
    """A receipt from a comment body, or from a comment node `{id, body,
    createdAt}` — the node's `createdAt` and `id` are what order receipts. A
    bare string is dated by its own `at`. None for anything else."""
    node = body if isinstance(body, dict) else None
    text = node.get("body") if node is not None else body
    if not isinstance(text, str):
        return None
    created = node.get("createdAt") if node is not None else None
    cid = (node.get("id") if node is not None else None) or ""
    return _parse(text, str(created or ""), str(cid))


@functools.lru_cache(maxsize=8192)
def _parse(text: str, created: str, cid: str) -> Receipt | None:
    """`parse_receipt` over immutable strings — memoized, because the ledger
    re-reads the same comments on every claim a sweep or a test settles."""
    lines = text.strip().splitlines()
    if not lines:
        return None
    parts = [p.strip() for p in lines[0].split(" · ")]
    head = _HEAD.match(parts[0])
    if not head or head.group(1) not in STATES:
        return None
    fields: dict = {}
    for part in parts[1:]:
        for key in _FIELDS:
            if part.startswith(key + " "):
                fields.setdefault(key, part[len(key) + 1:].strip())
                break
    if not all(fields.get(k) for k in _REQUIRED):
        return None
    place = of = None
    for line in lines[1:]:
        m = _PLACE.match(line.strip())
        if m:
            place, of = int(m.group(1)), int(m.group(2))
            break
    created = created or fields["at"]
    if _dt(created) is None:
        return None
    return Receipt(
        state=head.group(1), card=fields["card"], run=fields["run"],
        repo=fields["repo"], trigger=fields["trigger"], at=fields["at"],
        from_run=fields.get("from run") or None, reason=fields.get("reason") or None,
        because=fields.get("because") or None, created_at=created,
        comment_id=cid, place=place, of=of,
    )


def _key(r: Receipt) -> tuple:
    return (_dt(r.created_at), r.comment_id)


def _rid(r: Receipt) -> tuple:
    return (r.card, r.comment_id, r.run, r.created_at, r.state)


def _front(trigger: str) -> bool:
    return (trigger or "").strip().lower() == FRONT_TRIGGER


# --------------------------------------------------------------------------- #
# one card                                                                     #
# --------------------------------------------------------------------------- #


class _View:
    """One card's receipts read at `now`: which claims are open, which
    releases count, its state and its line entry."""

    def __init__(self, receipts: list, now: datetime, cfg: dict):
        self.now = now
        self.ttl = timedelta(minutes=cfg["claim_ttl_minutes"])
        self.grace = timedelta(minutes=cfg["dispatched_grace_minutes"])
        rs = sorted(receipts, key=_key)
        self.receipts = rs
        # The receipt that closes each claim: the first later receipt from the
        # same run, or the first later claim handed its slot.
        self.closer: dict = {}
        for i, r in enumerate(rs):
            if r.state != "claimed":
                continue
            for j in range(i + 1, len(rs)):
                q = rs[j]
                if q.run == r.run or (q.state == "claimed" and q.from_run == r.run):
                    self.closer[i] = j
                    break
        closing = {j: i for i, j in self.closer.items()}
        self.closing = closing
        # A claim inherits the slot key of the claim it closed (the handover),
        # so the slot keeps its sender's place in claim order.
        self.slot: dict = {}
        for i, r in enumerate(rs):
            if r.state != "claimed":
                continue
            k = closing.get(i)
            if k is not None and rs[k].state == "claimed" and r.from_run == rs[k].run:
                self.slot[i] = self.slot[k]
            else:
                self.slot[i] = _key(r)
        self.inherited = {i for i, r in enumerate(rs) if r.state == "claimed"
                          and self.slot[i] != _key(r)}
        self.unclosed = [i for i, r in enumerate(rs)
                         if r.state == "claimed" and i not in self.closer]
        self.open = [i for i in self.unclosed if not self._expired(rs[i])]
        self.expired = [i for i in self.unclosed if self._expired(rs[i])]
        # Releases that are about the CARD: they close a claim, and not as a
        # duplicate, or they are a park. Any other release is about a run and
        # moves nothing.
        self.ignored = {j for j, r in enumerate(rs) if r.state == "released"
                        and r.because != "parked"
                        and (j not in closing or r.because == "duplicate")}
        release_points = [j for j, r in enumerate(rs)
                          if r.state == "released" and j not in self.ignored]
        release_points += self.expired
        self.release_point = max(release_points, default=-1)

    @classmethod
    def of(cls, bodies, now, cfg, identifier: str | None = None) -> "_View":
        parsed = [parse_receipt(b) for b in (bodies or [])]
        return cls([r for r in parsed if r is not None
                    and (identifier is None or r.card == identifier)], now, cfg)

    def _expired(self, r: Receipt) -> bool:
        return self.now - _dt(r.created_at) >= self.ttl

    def open_claims(self) -> list:
        return [self.receipts[i] for i in self.open]

    def slot_key(self, r: Receipt) -> tuple:
        for i, x in enumerate(self.receipts):
            if x is r:
                return self.slot.get(i, _key(r))
        return _key(r)

    def effective(self) -> list:
        return [(i, r) for i, r in enumerate(self.receipts) if i not in self.ignored]

    def state(self) -> str | None:
        if self.open:
            return "claimed"
        eff = self.effective()
        if not eff:
            return None
        newest = eff[-1][1]
        if newest.state == "claimed":
            return "released"
        if newest.state == "dispatched":
            return ("dispatched" if self.now - _dt(newest.created_at) < self.grace
                    else "waiting")
        return newest.state

    def since_release(self) -> list:
        return [r for i, r in self.effective() if i > self.release_point]

    def line_entry(self) -> str | None:
        if self.state() not in ("waiting", "dispatched"):
            return None
        after = self.since_release()
        for state in ("waiting", "dispatched"):
            found = [r for r in after if r.state == state]
            if found:
                return found[0].created_at
        return None

    def _continued(self, run: str) -> Receipt | None:
        """The claim a re-run of `run` continues (DRE-5378): the one `run`'s
        own release closed, when that release is the card's newest. A GitHub
        re-run keeps its run id, so only a re-run can claim again under it."""
        p = self.release_point
        if p < 0 or p not in self.closing:
            return None
        rel = self.receipts[p]
        if rel.state != "released" or rel.run != run:
            return None
        return self.receipts[self.closing[p]]

    def arrival(self, claim: Receipt) -> str | None:
        """The place `claim` stands at if its run is refused: the card's own
        place just before it, or for a re-run the place the claim it
        continues had — that claim's own place, else its `createdAt`. None
        for a fresh arrival, which joins at the back."""
        prior = self.before(claim)
        held = prior._continued(claim.run)
        if held is not None:
            return prior.arrival(held) or held.created_at
        return prior.line_place()

    def line_place(self) -> str | None:
        """What orders this card in line: its line entry, unless the run that
        joined the line is a re-run, which keeps its first claim's place."""
        entry = self.line_entry()
        if entry is None:
            return None
        first = next((r for r in self.since_release() if r.state == "claimed"), None)
        return (self.arrival(first) if first is not None else None) or entry

    def line_receipt(self) -> Receipt | None:
        """The receipt that stands for this card in line: its newest `waiting`
        since its last release (it carries the reason it arrived with)."""
        after = self.since_release()
        waits = [r for r in after if r.state == "waiting"]
        if waits:
            return waits[-1]
        disp = [r for r in after if r.state == "dispatched"]
        return disp[-1] if disp else None

    def newest_effective(self) -> Receipt | None:
        eff = self.effective()
        return eff[-1][1] if eff else None

    def before(self, r: Receipt) -> "_View":
        """This card as it stood just before `r` was posted."""
        k = _key(r)
        return _View([x for x in self.receipts if _key(x) < k], self.now,
                     {"claim_ttl_minutes": self.ttl.total_seconds() / 60,
                      "dispatched_grace_minutes": self.grace.total_seconds() / 60})


def _now(now) -> datetime:
    return _dt(now) or _utcnow()


def open_claim(bodies, now=None) -> Receipt | None:
    claims = _View.of(bodies, _now(now), load()).open_claims()
    return claims[-1] if claims else None


def state(bodies, now=None) -> str | None:
    return _View.of(bodies, _now(now), load()).state()


def line_entry(bodies, now=None) -> str | None:
    return _View.of(bodies, _now(now), load()).line_entry()


def in_line(bodies, now=None) -> bool:
    return state(bodies, now) in ("waiting", "dispatched")


def waited_minutes(bodies, now=None) -> float | None:
    at = _now(now)
    entry = line_entry(bodies, at)
    if entry is None:
        return None
    return (at - _dt(entry)).total_seconds() / 60


def overdue(bodies, now=None) -> bool:
    waited = waited_minutes(bodies, now)
    return waited is not None and waited >= waiting_max()


# --------------------------------------------------------------------------- #
# the ledger                                                                   #
# --------------------------------------------------------------------------- #


@dataclass
class Ledger:
    running: list = field(default_factory=list)
    reserved: list = field(default_factory=list)
    waiting: list = field(default_factory=list)
    #: Every claim no receipt has closed, open or past the TTL — what
    #: `expired_claims` filters.
    unclosed: list = field(default_factory=list)
    #: Each waiting card's line entry (where its wait is measured from) and
    #: its place (what orders it: `_View.line_place`).
    entries: dict = field(default_factory=dict)
    places: dict = field(default_factory=dict)
    lanes: dict = field(default_factory=dict)
    now: datetime | None = None
    ttl_minutes: int = 0
    _slots: dict = field(default_factory=dict, repr=False)
    _views: dict = field(default_factory=dict, repr=False)

    def free_slots(self, cap: int) -> int:
        return max(0, cap - len(self.running) - len(self.reserved))

    def slot_key(self, r: Receipt) -> tuple:
        return self._slots.get(_rid(r), _key(r))


def ledger(cards, now=None, *, config: dict | None = None) -> Ledger:
    """The fleet's planner slots, read off cards in the sweep's shape
    (`identifier`, `state.name`, `comments.nodes[{id, body, createdAt}]`)."""
    cfg = config or load()
    at = _now(now)
    out = Ledger(now=at, ttl_minutes=cfg["claim_ttl_minutes"])
    line = []
    for card in cards or []:
        ident = card.get("identifier")
        lane = (card.get("state") or {}).get("name")
        if not ident or lane not in LEDGER_LANES:
            continue
        view = _View.of((card.get("comments") or {}).get("nodes"), at, cfg, ident)
        out._views[ident] = view
        out.lanes[ident] = lane
        for i, r in enumerate(view.receipts):
            if r.state == "claimed":
                out._slots[_rid(r)] = view.slot[i]
        out.unclosed.extend(view.receipts[i] for i in view.unclosed)
        claims = view.open_claims()
        if claims:
            out.running.append(min(claims, key=view.slot_key))
            continue
        if lane not in WAITING_LANES:
            continue
        current = view.state()
        if current == "dispatched":
            out.reserved.append(view.newest_effective())
        elif current == "waiting":
            out.entries[ident] = view.line_entry()
            place = out.places[ident] = view.line_place()
            stand = view.line_receipt()
            if stand is not None:
                line.append(((0 if _front(stand.trigger) else 1, _dt(place), ident), stand))
    out.waiting = [r for _, r in sorted(line, key=lambda pair: pair[0])]
    return out


def taken_before(ledger: Ledger, claim: Receipt) -> int:
    """The slots taken ahead of `claim`: every other card's open claim that is
    earlier in claim order, plus every other card's reserved slot."""
    mine = ledger.slot_key(claim)
    return (sum(1 for r in ledger.running
                if r.card != claim.card and ledger.slot_key(r) < mine)
            + sum(1 for r in ledger.reserved if r.card != claim.card))


def admits(ledger: Ledger, claim: Receipt, cap: int) -> bool:
    return taken_before(ledger, claim) < cap


def expired_claims(ledger: Ledger, now) -> list:
    at = _now(now)
    ttl = timedelta(minutes=ledger.ttl_minutes)
    return [r for r in ledger.unclosed if at - _dt(r.created_at) >= ttl]


def next_in_line(ledger: Ledger) -> Receipt | None:
    if ledger.waiting and ledger.free_slots(cap()) > 0:
        return ledger.waiting[0]
    return None


def _place(led: Ledger, mine: Receipt, limit: int) -> tuple[int, int]:
    """Where `mine` will stand once its `waiting` lands: `(place, of)`.

    Cards already waiting keep their line entries. A claimant that has not yet
    read but will be refused counts too: by its own earlier line entry if it
    has one, and otherwise BEHIND this card — its `waiting` will be posted
    after this one's.
    """
    far = datetime.max.replace(tzinfo=timezone.utc)

    def rank(front, entry, ident):
        return (0 if front else 1, 0 if entry else 1, entry or far, ident)

    my_entry = _dt(led._views[mine.card].arrival(mine))
    my_rank = rank(_front(mine.trigger), my_entry, mine.card)
    ahead, total = 0, 1
    for w in led.waiting:
        if w.card == mine.card:
            continue
        total += 1
        if rank(_front(w.trigger), _dt(led.places.get(w.card)), w.card) < my_rank:
            ahead += 1
    for r in led.running:
        if r.card == mine.card or led.lanes.get(r.card) not in WAITING_LANES:
            continue
        earlier_reserved = sum(1 for d in led.reserved
                               if d.card != r.card and _key(d) < _key(r))
        taken = sum(1 for x in led.running
                    if x.card != r.card and led.slot_key(x) < led.slot_key(r))
        if taken + earlier_reserved < limit:
            continue  # admitted, or will be
        total += 1
        entry = _dt(led._views[r.card].arrival(r))
        if entry is None and my_entry is None and _front(r.trigger) == _front(mine.trigger):
            continue  # fresh, and posting after this card
        if rank(_front(r.trigger), entry, r.card) < my_rank:
            ahead += 1
    return ahead + 1, total


# --------------------------------------------------------------------------- #
# the writes                                                                   #
# --------------------------------------------------------------------------- #


def _post(linear_ops, identifier: str, body: str) -> None:
    """THE one posting site of the planner queue."""
    condition = linear_ops.cmd_comment(identifier, body)
    if condition:
        raise PlannerQueueError(f"{identifier}: the planner-slot receipt did not land — "
                                f"{condition}")


def post_claim(linear_ops, identifier, *, run_id, repo, trigger_state,
               from_run=None) -> None:
    _post(linear_ops, identifier, format_receipt(
        "claimed", card=identifier, run=run_id, repo=repo, trigger=trigger_state,
        at=_iso(_utcnow()), from_run=from_run or None))


def post_waiting(linear_ops, identifier, *, run_id, repo, trigger_state, place=1,
                 of=1, reason=None) -> None:
    _post(linear_ops, identifier, format_receipt(
        "waiting", card=identifier, run=run_id, repo=repo, trigger=trigger_state,
        at=_iso(_utcnow()), reason=reason or None, place=place, of=of))


def post_released(linear_ops, identifier, *, run_id, repo, trigger_state,
                  because=None) -> None:
    _post(linear_ops, identifier, format_receipt(
        "released", card=identifier, run=run_id, repo=repo, trigger=trigger_state,
        at=_iso(_utcnow()), because=because or None))


def post_dispatched(linear_ops, identifier, *, run_id, repo, trigger_state,
                    reason=None) -> None:
    _post(linear_ops, identifier, format_receipt(
        "dispatched", card=identifier, run=run_id, repo=repo, trigger=trigger_state,
        at=_iso(_utcnow()), reason=reason or None))


# --------------------------------------------------------------------------- #
# the reads                                                                    #
# --------------------------------------------------------------------------- #


def ledger_query(window: int) -> str:
    """ONE paged read of the three ledger lanes, newest `window` comments each."""
    lanes = ", ".join(json.dumps(lane) for lane in LEDGER_LANES)
    return """query($after: String) {
       issues(first: 100, after: $after, filter: {
         team: {key: {eq: "DRE"}},
         state: {name: {in: [%s]}}
       }) { nodes {
         identifier updatedAt state { name }
         comments(first: %d) { nodes { id body createdAt } }
       } pageInfo { hasNextPage endCursor } } }""" % (lanes, window)


def read_board(linear_ops) -> list:
    return linear_ops.gql_paged(ledger_query(linear_ops.COMMENT_WINDOW), {})


def _read_card(linear_ops, identifier: str) -> dict:
    data = linear_ops.gql(CARD_COMMENTS_QUERY % linear_ops.COMMENT_WINDOW,
                          {"id": identifier}) or {}
    card = data.get("issue")
    if not card:
        raise PlannerQueueError(f"{identifier}: Linear returned no such card")
    return card


def _nodes(card: dict) -> list:
    return (card.get("comments") or {}).get("nodes") or []


# --------------------------------------------------------------------------- #
# claim                                                                        #
# --------------------------------------------------------------------------- #


def _answer(admitted: bool, place: int = 0, waiting: int = 0, duplicate: bool = False,
            inherited: bool = False) -> dict:
    return {"admitted": str(admitted).lower(), "place": str(place),
            "waiting": str(waiting), "duplicate": str(duplicate).lower(),
            "inherited": str(inherited).lower()}


def settle_claim(linear_ops, identifier, *, run_id, repo, trigger_state, reason=None,
                 cards=None, now=None, config=None, sent_by_run=None) -> dict:
    """The read half of a claim, after this run's `claimed` receipt is posted:
    admitted, a duplicate, or waiting at its place (and the receipt for it).

    `sent_by_run` covers the handover whose card read failed: an earlier open
    claim on this card held by the run that asked for this one is that run's
    slot passing on, not a duplicate. The card holds one slot either way —
    the ledger counts a card once — and the sender's release leaves this
    run's claim standing."""
    cfg = config or load()
    if cards is None:
        cards = read_board(linear_ops)
    led = ledger(cards, now=now, config=cfg)
    view = led._views.get(identifier)
    mine = None
    if view is not None:
        own = [r for r in view.open_claims() if r.run == str(run_id)]
        mine = own[-1] if own else None
    if mine is None:
        raise PlannerQueueError(
            f"{identifier}: this run's claim is not on the ledger read")
    mine_key = led.slot_key(mine)
    earlier = [r for r in view.open_claims()
               if r.run != mine.run and led.slot_key(r) < mine_key]
    if earlier and sent_by_run and all(r.run == str(sent_by_run) for r in earlier):
        return _answer(True, waiting=len(led.waiting), inherited=True)
    if earlier:
        post_released(linear_ops, identifier, run_id=run_id, repo=repo,
                      trigger_state=trigger_state, because="duplicate")
        return _answer(False, waiting=len(led.waiting), duplicate=True)
    if admits(led, mine, cfg["max_running"]):
        return _answer(True, waiting=len(led.waiting))
    place, of = _place(led, mine, cfg["max_running"])
    post_waiting(linear_ops, identifier, run_id=run_id, repo=repo,
                 trigger_state=trigger_state, place=place, of=of, reason=reason)
    return _answer(False, place=place, waiting=of)


def _handover(linear_ops, identifier, *, run_id, repo, trigger_state, sent_by_run,
              cfg) -> str:
    """`inherited`, `posted` (the claim is written but its sender's claim had
    already closed), or `ordinary` (nothing written: claim as any run does)."""
    try:
        card = _read_card(linear_ops, identifier)
    except Exception as exc:  # noqa: BLE001 — the claim must still be posted
        _warn(f"planner slot for {identifier}: could not read the card for the "
              f"handover, claiming as any run does — {exc}")
        return "ordinary"
    held = _View.of(_nodes(card), _utcnow(), cfg, identifier).open_claims()
    if not held or held[-1].run != str(sent_by_run):
        return "ordinary"
    post_claim(linear_ops, identifier, run_id=run_id, repo=repo,
               trigger_state=trigger_state, from_run=sent_by_run)
    # Read back: S may have released between the read above and the write.
    try:
        card = _read_card(linear_ops, identifier)
    except Exception:  # noqa: BLE001 — the claim is posted; settle it the long way
        return "posted"
    view = _View.of(_nodes(card), _utcnow(), cfg, identifier)
    for i, r in enumerate(view.receipts):
        if (r.state == "claimed" and r.run == str(run_id)
                and r.from_run == str(sent_by_run) and i in view.inherited
                and i in view.open):
            return "inherited"
    return "posted"


def _warn(message: str) -> None:
    print("::warning::" + " ".join(str(message).split()))


def claim(linear_ops, identifier, *, run_id, repo, trigger_state, sent_by_run=None,
          reason=None) -> dict:
    """Claim a planner slot for this run. Fails OPEN: a run is never refused on
    a read that did not happen."""
    reason = reason or None
    try:
        cfg = load()
    except PlannerQueueError as exc:
        _warn(f"planner slot for {identifier}: admitted without a cap — {exc}")
        try:
            post_claim(linear_ops, identifier, run_id=run_id, repo=repo,
                       trigger_state=trigger_state)
        except Exception as post_exc:  # noqa: BLE001 — fail open
            _warn(f"planner slot for {identifier}: the claim did not post — {post_exc}")
        return _answer(True)
    try:
        posted = False
        if sent_by_run:
            verdict = _handover(linear_ops, identifier, run_id=run_id, repo=repo,
                                trigger_state=trigger_state, sent_by_run=sent_by_run,
                                cfg=cfg)
            if verdict == "inherited":
                return _answer(True, inherited=True)
            posted = verdict == "posted"
        if not posted:
            post_claim(linear_ops, identifier, run_id=run_id, repo=repo,
                       trigger_state=trigger_state)
        return settle_claim(linear_ops, identifier, run_id=run_id, repo=repo,
                            trigger_state=trigger_state, reason=reason, config=cfg,
                            sent_by_run=sent_by_run)
    except Exception as exc:  # noqa: BLE001 — fail open, loudly
        _warn(f"planner slot for {identifier}: admitted without a ledger read — {exc}")
        return _answer(True)


# --------------------------------------------------------------------------- #
# CLI                                                                          #
# --------------------------------------------------------------------------- #


def _write_outputs(path: str | None, pairs: list) -> None:
    for key, value in pairs:
        print(f"{key}={value}")
    if not path:
        return
    try:
        with open(path, "a", encoding="utf-8") as fh:
            for key, value in pairs:
                fh.write(f"{key}={' '.join(str(value).split())}\n")
    except OSError as exc:
        _warn(f"planner queue: could not write step outputs: {exc}")


def _cmd_check(args) -> int:
    try:
        cfg = load()
    except PlannerQueueError as exc:
        print(f"planner queue config: {exc}", file=sys.stderr)
        return 2
    print(f"planner queue config OK: {config_path()} "
          f"(max_running={cfg['max_running']})")
    return 0


def _cmd_claim(args) -> int:
    import linear_ops  # noqa: PLC0415 — `check` must run with this file alone
    out = claim(linear_ops, args.card, run_id=args.run_id, repo=args.repo,
                trigger_state=args.trigger_state, sent_by_run=args.sent_by_run or None,
                reason=args.reason or None)
    _write_outputs(args.github_output, list(out.items()))
    return 0


def _cmd_release(args) -> int:
    import linear_ops  # noqa: PLC0415
    try:
        post_released(linear_ops, args.card, run_id=args.run_id, repo=args.repo,
                      trigger_state=args.trigger_state, because=args.because or None)
    except Exception as exc:  # noqa: BLE001 — a release never fails the step
        _warn(f"planner slot for {args.card}: the release did not post — {exc}")
    return 0


def _cmd_next(args) -> int:
    import linear_ops  # noqa: PLC0415
    found = None
    try:
        found = next_in_line(ledger(read_board(linear_ops)))
    except Exception as exc:  # noqa: BLE001
        _warn(f"planner queue: could not read the line — {exc}")
    owner, _, name = (found.repo if found else "").partition("/")
    _write_outputs(args.github_output, [
        ("card", found.card if found else ""),
        ("repo", found.repo if found else ""),
        ("owner", owner),
        ("name", name),
        ("trigger_state", found.trigger if found else ""),
        ("reason", (found.reason or "") if found else ""),
    ])
    return 0


def _cmd_dispatch(args) -> int:
    import linear_ops  # noqa: PLC0415
    import plan_run  # noqa: PLC0415
    try:
        record = (linear_ops.gql(plan_run.CARD_QUERY, {"id": args.card}) or {}).get("issue")
    except Exception as exc:  # noqa: BLE001
        _warn(f"planner queue: could not read {args.card} to dispatch it — {exc}")
        return 0
    if not record:
        _warn(f"planner queue: Linear returned no card {args.card}; it stays waiting")
        return 0
    try:
        # The plan event, whatever the labels: a card in the line is waiting
        # to be planned, and the label rule would build a one-off (DRE-5366).
        ok, err = plan_run.fire(record, args.repo, trigger_state=args.trigger_state,
                                reason=args.reason or None, event=plan_run.PLAN_EVENT)
    except Exception as exc:  # noqa: BLE001
        ok, err = False, str(exc)
    if not ok:
        _warn(f"planner queue: dispatch of {args.card} failed; it stays waiting — {err}")
        return 0
    try:
        post_dispatched(linear_ops, args.card, run_id=args.run_id, repo=args.repo,
                        trigger_state=args.trigger_state, reason=args.reason or None)
    except Exception as exc:  # noqa: BLE001
        _warn(f"planner queue: {args.card} was dispatched but its receipt did not post "
              f"— {exc}")
    print(f"dispatched {args.card} at {args.repo}")
    return 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = ap.add_subparsers(dest="cmd", required=True)

    c = sub.add_parser("check", help="validate config/planner-queue.json")
    c.set_defaults(fn=_cmd_check)

    c = sub.add_parser("claim", help="claim a planner slot for this run")
    c.add_argument("card")
    c.add_argument("--run-id", required=True)
    c.add_argument("--repo", required=True)
    c.add_argument("--trigger-state", required=True)
    c.add_argument("--sent-by-run", default="")
    c.add_argument("--reason", default="")
    c.add_argument("--github-output", default=None)
    c.set_defaults(fn=_cmd_claim)

    c = sub.add_parser("release", help="release this run's slot")
    c.add_argument("card")
    c.add_argument("--run-id", required=True)
    c.add_argument("--because", default="")
    c.add_argument("--repo", default=os.environ.get("GITHUB_REPOSITORY") or "-")
    c.add_argument("--trigger-state", default="-")
    c.set_defaults(fn=_cmd_release)

    c = sub.add_parser("next", help="the card the next free slot serves")
    c.add_argument("--github-output", default=None)
    c.set_defaults(fn=_cmd_next)

    c = sub.add_parser("dispatch", help="start a waiting card's planner run")
    c.add_argument("card")
    c.add_argument("--run-id", required=True)
    c.add_argument("--repo", required=True)
    c.add_argument("--trigger-state", required=True)
    c.add_argument("--reason", default="")
    c.set_defaults(fn=_cmd_dispatch)

    args = ap.parse_args(argv)
    return args.fn(args)


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
