# ADR: no fixed number of planners — a planner reads the planners' Linear allowance before it starts

- **Status:** Accepted — 2026-10-31, the CEO's decision on epic
  [DRE-8539](https://linear.app/dreadnoughtfoundry/issue/DRE-8539). The
  replacement is built by
  [DRE-8616](https://linear.app/dreadnoughtfoundry/issue/DRE-8616):
  `scripts/planner_allowance.py` and `config/planner-allowance.json`. The plan
  workflow is wired to it by DRE-8617, and the cap's own code leaves in the
  epic's other cards.
- **Date:** 2026-11-01
- **Supersedes:** the fleet-wide planner cap — `config/planner-queue.json`'s
  `max_running` and its ceiling, the `PLANNER_MAX_RUNNING` repo variable, and
  the `🎟️ planner-slot:` ledger of `scripts/planner_queue.py` (DRE-5176, epic
  DRE-5167).

## Context

On 2026-09-28 nineteen planner runs released in one minute and drained the one
Linear key every planner shared. The answer that day was a cap: at most four
planners running across the fleet, later two, then three. The cap was a whole
construct — a number, a ceiling, a published repo variable, a slot ledger kept
as receipts on the cards, a waiting line with places, a groom drain that
released no more cards than there were free slots, and a console control.

Two things have changed since. The planners spend their own 5,000-an-hour
OAuth bucket rather than the fleet's key (DRE-5589), and the planner itself
spends far fewer Linear requests per run. The cap now holds planners back
while the allowance it protects sits mostly unused.

## Decision

On 2026-10-31 the CEO asked for the cap to go:

> "We need to put a card in to remove the max number of planners. Before we
> couldn't run 4 planners but we can run as many as we want now because we've
> basically optimized how to do the planners."

> "Make sure you go through the entire codebase and have a clean [code] base so
> it's just smart enough to know when we're basically doing too much planning."

He was offered three options:

1. **No limit, no safety net** — no count and no check of any kind.
2. **Keep a high cap instead** (for example 20) — the same construct with a
   larger number.
3. **"No limit, slow down near empty"** — no fixed count of planners; only
   when Linear reports the planners' allowance is nearly used up do new
   planners wait a few minutes, then go.

He chose the third.

## What replaced the cap

`scripts/planner_allowance.py`, read before a planner starts. It sends one
cheap request through `linear_ops.gql` on the planner's own key and reads
Linear's `x-ratelimit-requests-remaining` header. At or above
`threshold_remaining` the planner starts at once, however many others are
running, and nothing is posted. Below it — and a `RATELIMITED` answer counts as
below — the planner waits to the next `wait_minutes` boundary and reads again,
posting one `🪫 planner-hold: held` receipt so the sweep and the console can
see why it waits. When a read is good, or `hold_max_minutes` has passed
whatever the reading, it starts after a short stagger in the held line's order
and posts `🪫 planner-hold: started`. A read that fails for any other reason
starts the planner at once with a warning: the allowance check never fails or
strands a planner.

The numbers live in `config/planner-allowance.json`, and `planner_allowance.py
check` keeps the whole wait — `hold_max_minutes` plus `wait_minutes`, the
stagger included — below the Planning stall window, so a waiting planner is
never read as a stalled card.

`linear_ops.gql` stops a process asking after Linear's first `RATELIMITED`
(DRE-3202), which is right for every caller that would ask again at once. The
wait is the one caller that has already backed off, so it lifts that stop with
`linear_ops.lift_rate_limit_stop` after each sleep to a boundary, and only
then. It is the function's only caller, and a test holds it to that.

## What was kept

- **One planner per card.** A second plan run on a card that is already being
  planned is still refused, by the duplicate-dispatch guard
  (`dedupe_dispatch.py plan-gate` in `plan.yml`), which never depended on the
  cap.
- **The CEO's Planning order.** When planners are held by the slow-down, the
  order he sets on the Overview decides who starts first: the held line is
  ordered by it, then oldest `held` first.
- **The planners' own allowance.** The planners keep the 5,000-an-hour OAuth
  bucket (DRE-5589); the slow-down reads that bucket and no other.

## Consequences

- Any number of planners may run at once while the allowance is healthy. The
  fast path costs one Linear request per planner run.
- A held planner is bounded: it starts by `hold_max_minutes` past its first
  read, plus at most `wait_minutes` of stagger.
- A refusal of Linear's planners' bucket is proven by transport tests rather
  than provoked live, because emptying the shared bucket would stop every
  planner in the fleet for up to an hour. The live proof (DRE-8624) observes a
  hold through the sandbox's declared fixture, `fixture_low_reading`.
