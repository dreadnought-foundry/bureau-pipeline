# A queued build run read as no run at all — portico, 2026-09-24 (DRE-4830)

The record for the fix in this branch, in the shape
`docs/review-rerun-proof-2026-09.md` already uses: the receipts first, then what
they name, then what changed. It is here because the receipts are not durable —
a Linear comment window rolls — and because the finding is about a writer nobody
had named.

All times below are as Linear stores them (UTC). PT is UTC−7.

## The writer

**The reconcile sweep's nudge loop** — the `elif state == "Todo" and not
is_open:` branch of `reconcile.main()`, which fires a fresh `agent-execute`
repository_dispatch through `redispatch(card)` and then posts
`reconcile._TODO_REDISPATCH_NOTE`.

| Card | promoted to Todo | the re-dispatch receipt |
| -- | -- | -- |
| [DRE-4518](https://linear.app/dreadnoughtfoundry/issue/DRE-4518) | `2026-09-24T21:59:34Z` — 14:59:34 PT | `2026-09-24T22:15:58Z` — **15:15:58 PT** |
| DRE-4519 | `21:59:36Z` — 14:59:36 PT | `22:15:49Z` — **15:15:49 PT** |
| DRE-4526 | `22:00:24Z` — 15:00:24 PT | `22:29:19Z` — **15:29:19 PT** |

All three carry the identical line:

> `🧹 Reconcile: card sat in Todo with no run — re-dispatched.`

That string has exactly one writer in the fleet — `_TODO_REDISPATCH_NOTE`,
posted only by the branch above. It was **not** the relay on a lane move: no
lane moved, the cards sat in Todo throughout. It was **not** epic promotion
re-firing: the `🧹 Auto-promoted Backlog → Todo` receipt posted once per card,
at 14:59 and 15:00.

The sixteen-minute gap is arithmetic, not coincidence. `config/lane-contract.json`
gives Todo a 15-minute stall window and the sweep runs every 15 minutes.

## Why the sweep could not see the first run

`agent-task.yml` posts its `🧠 model-attempt` heartbeat at the **Card → In
Progress** step, which runs only once the job has a runner. The mini was
saturated all afternoon — 78 jobs waiting at 17:00 — so every one of those runs
sat `queued` and had posted nothing.

So the evidence the Todo branch reads — *in Todo, no pull request, no run
receipt* — is **identical** for "no run was ever dispatched" and "a run is
waiting for a runner". `agent_run_alive` already counted a queued run as alive,
but it reads that same heartbeat, so it had nothing to read either; and the Todo
branch never consulted it.

## What the duplicate then did

Five of the duplicates skipped hours later on the duplicate-dispatch guard's
open-PR condition, each having held a queue place and a runner slot for its setup
steps. DRE-4518's did not, on this timeline from the card's own thread:

| time | what |
| -- | -- |
| `2026-09-25T00:24:20Z` — 17:24 PT | `✅ Merged: …/portico/pull/700` |
| `00:37:24Z` — 17:37 PT | `🧠 model-attempt … engineer agent starting` — the duplicate got a runner |
| `00:40:03Z` | `🤖 PR already merged: … no requeue` — reported **after** the build, not gated before it |

It rebuilt the same three tools from scratch onto the merged branch — commits
`31eda89a` and `e4bcd37b` — which is the stranded-commit card DRE-4828,
cancelled as a duplicate.

## What changed

1. **`agent-task.yml` names its job after the card** — `bureau-card: <DRE-N>`,
   the convention `plan.yml` has carried since DRE-3223. It is the only place a
   card identifier survives into the Actions API for a run that has not started,
   so it is the only way a queued run can be attributed to a card at all.
2. **`reconcile.build_run_refusal()` asks GitHub instead of the card.** A run of
   this repo's build stub that GitHub says has not finished — `queued`,
   `waiting`, `pending`, `requested` or `in_progress`, all counted identically —
   refuses the Todo re-dispatch. The status set is
   `stranded_fix.IN_FLIGHT_STATUSES`, the one declaration; the job-name match is
   `dedupe_dispatch`'s one parse. A card with no run at all still dispatches.
3. **The duplicate-dispatch gate refuses work that already shipped** — a MERGED
   agent PR, or a card whose own lane is the review lane or Done. A
   CLOSED-unmerged PR still rebuilds: that attempt was abandoned and the card
   still owes work, which is the line `card_pr.has_work_pr` draws.

## The fail directions, which are not the same in the two writers

* **The sweep fails CLOSED.** An unreadable Actions listing defers one dispatch
  for fifteen minutes and takes the sweep red for the medic —
  `gh_actions_read`'s standing contract, and `_actions_runs_busy`'s precedent. A
  repo with no build stub is adjudicated as "nothing in flight" and stays green
  (DRE-4378), so a missing stub cannot make every sweep red forever.
* **The gate fails OPEN**, as it always has: it gates a build, not a merge, and a
  missed skip is the status quo while a false skip strands a healthy card.

## Still blind, deliberately

`flag_stranded`'s no-run class has the same blind spot and already documents it
as an accepted false positive — *"If a run is merely queued, remove the
`needs-human` label and it will carry on"*. It fired for real on DRE-4519 at
15:29 PT. It alarms and labels rather than dispatching, so it is not the harm
DRE-4830 names, and `build_run_refusal` is now available if that trade is ever
revisited. Changing what the watchdog reports is a decision of its own.
