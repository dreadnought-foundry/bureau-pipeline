# The stale merge-ref refresh, observed on the live board

**DRE-3145.** Read by hand on 2026-09-10 between 06:46 and 06:53 PT against the
live GitHub state of `dreadnought-foundry/bureau-pipeline` and
`dreadnought-foundry/agent-bureau`. Nothing here was written, dispatched or
re-run: every line below came from a `gh` read of a sweep log, a pull request,
or a commit's check runs.

Times are Pacific (PDT, UTC−7) and labelled. Shas are full where they identify
a commit the record depends on.

## The headline

**The rule is live and it is deciding on real pull requests every sweep. It has
not yet had a pull request to refresh, so there is no refresh to record.**

In the window read — 53 bureau-pipeline sweeps from 2026-09-09 17:27 PT to
2026-09-10 06:47 PT, and 60 agent-bureau sweeps from 2026-09-09 18:39 PT to
2026-09-10 06:46 PT — the sweep evaluated pull requests on both repositories and
declined every one of them, each time with the reason printed. No
`update-branch` was issued by the rule, and no `merge-ref-refreshed` receipt
exists on any pull request or card in either repository.

That makes acceptance criteria 1, 2 and 4 **not yet observable**; criteria 3 and
5 are recorded below, and criterion 3 holds twice over. This document is the
honest state, not a pass.

## What is deployed, and where

| Fact | Value |
| --- | --- |
| Rule reached bureau-pipeline `main` | `763f0e93` — merge of PR #303, 2026-09-07 16:14 PT |
| bureau-pipeline's own sweep runs | `.github/workflows/self-reconcile.yml`, `reconcile.yml@main`, `pipeline_ref: main` |
| agent-bureau's sweep runs | `.github/workflows/reconcile.yml`, `reconcile.yml@stable`, `pipeline_ref: stable` |
| `stable` at the time of reading | `b860c24144b66e918117a09d17e7bcac08cfcf7d`, which contains `763f0e93` |
| Console's act row | `console/backend/receipts.py`, commit `1e8e0e62f`, 2026-09-05 09:09 PT |

So the rule is live in bureau-pipeline directly and in agent-bureau through the
`stable` channel. Both sweeps ran it in the window read.

## 1. A live refresh — NOT OBSERVED

No pull request in either repository carries the act. Searched:

* every comment on the most recent 60 pull requests of `bureau-pipeline` and the
  most recent 60 of `agent-bureau` (`gh pr list --state all --limit 60 --json
  number,comments`), for the three strings the act can only appear as —
  `stale-merge-ref-refresh` (the idempotency marker), `merge-ref-refreshed` (the
  `📎 pipeline-act:` trailer) and `refreshed the merge ref` (the anchor phrase in
  `stale_merge_ref.ANCHOR_PHRASE`). **Zero hits in either repository.**
* every sweep log in the window above, for the line that
  `reconcile._refresh_one_merge_ref` prints before it writes:

  ```
  stale-merge-ref: PR #<n> <head sha> — <reason>; refreshing the merge ref onto `main` <main sha>
  ```

  **Zero hits.**

### Why the 2026-09-09 red main did not produce one

`main` went red on 2026-09-09 at 17:48 PT and was fixed the same evening. Two
pull requests carried `🧬 inherited-failure` notes that night. Neither could
reach this rule, and for two different reasons:

* **bureau-pipeline #335** (`agent/DRE-3366-groom-drain-dispatch`, opened
  2026-09-09 16:24 PT) merged at 2026-09-10 06:33 PT, head
  `01a9edbb6f71a943eea394050f7f5a7f4fb9beac`. It went green and merged on its
  own before any sweep found it both red and mergeable.
* **bureau-pipeline #333** (`agent/DRE-3453-gate-paths-fail-fast`, opened
  2026-09-09 15:04 PT, head `380e5eaf73d9895d8dcdbbadbdce4370e9b5a830`) is
  `DIRTY` — conflicted with `main`. `refresh_stale_merge_refs` skips a `DIRTY`
  pull request **before** it reads anything, because `update-branch` cannot
  resolve a conflict (DRE-2416) and `unstick_conflicts` owns it. #333 therefore
  never appears in a single sweep log line, which is exactly what the code says
  should happen.

An `🧬 inherited-failure` note is a diagnosis, not this act. Finding one is not
finding a refresh, and this record does not treat it as one.

### What would produce one

A pull request that, at the moment of a **full** sweep, is all of:

1. on an `agent/*` (or `repair/*`) branch, not a draft, not `DIRTY`, and not
   parked by a human;
2. `behind_by > 0` — `main` has moved past its merge base;
3. red on at least one non-review check (checks whose name ends in `review` are
   excluded by design, see below);
4. red on **every** one of those checks on its merge base too; and
5. green on **every** one of them on `main`'s tip, with those runs completed.

In practice: `main` breaks a shared check, a pull request's CI runs against the
merge ref computed before the fix, the fix merges and `main`'s CI finishes
green, and the pull request stays mergeable until the next 15-minute sweep.
The 2026-09-02 shape (agent-bureau #2240 and #2241) is precisely this; it has
not recurred with a mergeable pull request since the rule went live.

## 2. No second refresh — NOT OBSERVABLE

Follows from item 1: with no first refresh there is no second to rule out. The
idempotency key is in place and unexercised — `stale_merge_ref.marker()` writes
`stale-merge-ref-refresh @<40-hex main sha>` as the first line of the receipt,
and the next sweep counts it out of `pr.comments` before deciding.

The closest live analogue was observed and is recorded in item 3: the same pull
request, re-decided identically on nine consecutive sweeps, with no comment
posted on any of them.

## 3. A pull request left alone — OBSERVED, TWICE

### bureau-pipeline #344

`agent/DRE-3356-ledger-discovers-its-population`, head
`073a72235267cf272dfa056868860cbf643d395e`, opened 2026-09-09 18:42 PT.
<https://github.com/dreadnought-foundry/bureau-pipeline/pull/344>

The sweep log line, quoted verbatim:

```
stale-merge-ref: PR #344 — own: scripts unit tests is green on the merge base — this pull request's own defect, and the fix loop owns it
```

Printed by three consecutive full sweeps:

| Run | Log timestamp | PT |
| --- | --- | --- |
| [34426963497](https://github.com/dreadnought-foundry/bureau-pipeline/actions/runs/34426963497) | `2026-09-10T01:49:54Z` | 2026-09-09 18:49 PT |
| [34427336783](https://github.com/dreadnought-foundry/bureau-pipeline/actions/runs/34427336783) | `2026-09-10T01:55:11Z` | 2026-09-09 18:55 PT |
| [34428708908](https://github.com/dreadnought-foundry/bureau-pipeline/actions/runs/34428708908) | `2026-09-10T02:15:34Z` | 2026-09-09 19:15 PT |

Checked against the head's own check runs, read 2026-09-10 06:50 PT:

```
QA critic review :: failure
scripts unit tests :: failure
```

`scripts unit tests` is the check the sweep named. The rule read it as this
branch's own defect and left it alone: no `update-branch`, no comment on the
pull request, no comment on the card.

Note the second line. `QA critic review` is red on this head too, and the rule
never considered it — `stale_merge_ref._is_review_check` drops every check whose
name ends in `review` before the failing set is built, the same filter
`reconcile.fix_approved_but_red` uses. That exclusion is visible in live data
here, not only in the tests.

### agent-bureau #2428

`agent/DRE-3377-groom-proposal-row`, head
`1fbdf92b1c51b96b7d41b2ed21b0497a6e7ba08d`, opened 2026-09-09 22:10 PT, since
closed.
<https://github.com/dreadnought-foundry/agent-bureau/pull/2428>

```
stale-merge-ref: PR #2428 — own: Console web (vitest) (2) is green on the merge base — this pull request's own defect, and the fix loop owns it
```

Printed by **nine** consecutive full sweeps, 2026-09-09 22:23 PT through
2026-09-09 23:38 PT: runs 34440833031, 34440871346, 34441541062, 34441661365,
34441878222, 34442400306, 34442797133, 34443926463, 34446061450.

The head's check runs, read 2026-09-10 06:50 PT:

```
Console web (vitest) (1) :: cancelled
Console web (vitest) (2) :: failure
QA critic review :: failure
```

Nine identical decisions, nine sweeps, and not one comment written. A rule that
declines is meant to be silent, and it was.

### The other decisions in the window

Recorded because they show the rule reading real payloads and answering cheaply,
not because the card asks for them:

* `current` — "`main` has not moved past the merge base — a refresh would
  change nothing": bureau-pipeline #342; agent-bureau #2419, #2420, #2421,
  #2423.
* `no-failure` — "no failing checks on this head": bureau-pipeline #341, #343;
  agent-bureau #2422, #2424, #2425, #2429, #2430, #2431.

No `main-still-red`, `unevaluated`, `already-refreshed` or `cap-spent` decision
appeared in the window.

## 4. Did the critic verdict carry — NOT OBSERVABLE

There was no refresh, so there was no refreshed head for
`should_review_pr.py` to decide about, and this record makes no claim either
way.

What the mechanism promises, so the next reader knows what to look for: an
`update-branch`-only head change leaves the three-dot diff against `main`
unchanged, so content binding (DRE-2340, `verdict_content.py`) keeps the
standing critic verdict and `should_review_pr.py` skips the re-review. The
receipt says so in its own body, and says when it will not carry — if `main`'s
fix touched a file the branch also touches, the diff changed, the verdict is
discharged, and a fresh review runs.

**No sibling card has been filed**, because the finding the card describes — a
fresh review running on an unchanged diff — cannot be observed until a refresh
happens. Whoever records the first refresh owes this line an answer.

## 5. What the console shows — REGISTRY CONFIRMED, LIVE SCREENSHOT OWED

The console already knows the act. `console/backend/receipts.py` (agent-bureau,
commit `1e8e0e62f`, "feat(DRE-3137): the console learns the
stale-merge-ref-refresh act", 2026-09-05 09:09 PT) declares:

```python
Act("stale-merge-ref-refresh", "merge-ref-refreshed", RECOVERY,
    "This PR was red on a problem since fixed on the main branch — the "
    "sweep refreshed it and the checks are running again",
    cadence_s=_REVIEW_BOUND),
```

`RECOVERY` is the kind the card asks for, and the sentence above is what a
reader would see. The row shipped in release `agent-bureau-console-v1.6.16` and
is in every console release since, so the deployed console carries it.

bureau-pipeline's own registry agrees: `config/pipeline-acts.json` declares
`merge-ref-refreshed` with `"kind": "recovery"`, `"adopted": true`,
`"next_actor": "qa-review.yml"`, and the anchor
`refreshed the merge ref: the fault was on main, not in this pull request`.

**Owed:** a screenshot of the live console showing a real receipt on a real
card. It cannot be taken today for two reasons — there is no receipt to show,
and `https://app.agent-bureau.com` answers `302` to an unauthenticated read, so
the check needs an operator's signed-in browser. Take it with the first
refresh.

## Where this leaves the card

| Acceptance criterion | State |
| --- | --- |
| One refresh observed, with PR receipt and card receipt | **Not met** — no refresh has occurred |
| The same pull request not refreshed again on the next sweep | **Not observable** yet |
| A pull request with its own failure left alone, log line quoted | **Met** — bureau-pipeline #344 and agent-bureau #2428 |
| Whether the critic verdict carried | **Not observable** yet; no sibling card filed |
| What the live console showed | **Partly met** — the act row is declared and shipped; the live screenshot is owed |
| `docs/stale-merge-ref-proof.md` merged to `main` | This document |

The mechanism is deployed and demonstrably deciding. What is missing is an
occasion, and an occasion is a broken `main` plus a mergeable pull request —
not something to manufacture on the live board.

---

Read and written 2026-09-10, 06:46–06:53 PT, from
`dreadnought-foundry/bureau-pipeline` and `dreadnought-foundry/agent-bureau`.
Every observation is a read; nothing on either board was changed.
