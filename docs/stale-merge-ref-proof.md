# The stale merge-ref refresh, observed on the live board

## 2026-10-02: a refresh, staged and observed on the sandbox

**The rule worked once, on one pull request, against one `main` commit, and did
nothing on the next sweep. Its sibling, red on its own failure, was left alone
on both sweeps.** This was staged on the pipeline's sandbox,
`dreadnought-foundry/bureau-harness` ("Pipeline integration-harness sandbox",
DRE-2073), not on a production repository, on the CEO's instruction of
2026-10-02 to clear the pipeline's proof cards with automated tests and without
involving anyone. An agent session did the staging and the reading, and nobody
operated anything by hand. The 2026-09-10 reading below is kept as it was
written. This section answers the items it left "not observable".

Times are Pacific (PDT, UTC−7). Run ids link to `bureau-harness` Actions.

### Why the sandbox can carry it

* **A check that a `main` fault turns red for every pull request.** The
  harness's own `ci.yml` job `test` runs on `pull_request` and on `push` to
  `main`. Its push run on a `main` commit is the merge-base evidence the rule
  reads.
* **The same sweep.** The harness `Reconcile` stub calls
  `bureau-pipeline/.github/workflows/reconcile.yml@main` with
  `pipeline_ref: main`, on a `*/15` schedule and by `workflow_dispatch`.
  `refresh_stale_merge_refs` is not one of the eight phases an off-rail sweep
  skips (`OFF_RAIL_SKIPPED`), and `STALE_MERGE_REFRESH_CAP` is unset there, so
  it defaults to 3.
* **The rule's gates admit a harness pull request.** The branch is `agent/`,
  the PR is not a draft and not `DIRTY`, and `fix_dispatch_blocked` consults
  Linear only for a branch carrying `DRE-n`. The fixture branches carried none
  on purpose, so nothing was read from or written to a card.

No workflow was enabled for this, in any repository. Both sweeps were
`workflow_dispatch` runs of the harness's already-active `Reconcile` workflow.

### The staging

| Step | What | Sha / id | PT |
| --- | --- | --- | --- |
| M0 | harness `main` before the proof, `test` green | `47299ff52e0f7841f48c53b2dd96b660bd96d51c` | — |
| B | `agent/proof3145-own-failure`, cut from M0, adds a test that fails on purpose | head `81f92aaa327fdb2ec63aeaa7195ea07dac8e245e` | 2026-10-02 16:55:01 |
| | PR [#3099](https://github.com/dreadnought-foundry/bureau-harness/pull/3099) opened; CI [37079798932](https://github.com/dreadnought-foundry/bureau-harness/actions/runs/37079798932) `test` failure | | 16:55:09 |
| | critic `REQUEST_CHANGES cause:defect @81f92aaa…` (QA Review [37079799565](https://github.com/dreadnought-foundry/bureau-harness/actions/runs/37079799565)) | | 16:56:28 |
| M1 | the fault: `tests/test_proof3145_main_fault.py` committed to `main` | `fc09c2ad679fbba4aad3a9990efa76d481873238` | 16:57:04 |
| | M1 push CI [37079933906](https://github.com/dreadnought-foundry/bureau-harness/actions/runs/37079933906): `test` failure | | 16:57:07 |
| A | `agent/proof3145-main-fault`, cut from M1. It merges B's head, deletes B's failing test, and adds one harmless file | head `f4f066ef33c7a97093a0aaf5069f7b7f1105f979` | 16:57:09 |
| | PR [#3100](https://github.com/dreadnought-foundry/bureau-harness/pull/3100) opened | | 16:57:17 |
| | A's CI [37079949172](https://github.com/dreadnought-foundry/bureau-harness/actions/runs/37079949172) on merge ref `d7588d1` (A into M1): `FAILED tests/test_proof3145_main_fault.py::…`, `1 failed, 5 passed` | | 16:57:24–16:57:35 |
| M2 | the fix: the fault file deleted from `main`. The tree is `01e05fa2…`, identical to M0's | `9596d756df34fdebd841f6c9f8d3d85156f1b839` | 16:57:40 |
| | M2 push CI [37079973820](https://github.com/dreadnought-foundry/bureau-harness/actions/runs/37079973820): `test` success | | 16:57:58 |
| | critic `APPROVE @f4f066ef…` on A (QA Review [37079949604](https://github.com/dreadnought-foundry/bureau-harness/actions/runs/37079949604)) | | 16:58:57 |

`main` was red for 36 seconds (16:57:04–16:57:40). No Integration Harness run
was in flight in that window. The last one, bureau-pipeline run 37078484136,
had finished before M1, and none started until after cleanup.

**Why A carries B's head.** Without it, the merge gate would have merged A
minutes after the refresh: CI turns green, and the APPROVE carries. A
deliberately carries B's head commit, so the gate's stack rule (DRE-4103) holds
A while B stands unapproved. A's own net diff against `main` is one harmless
file, `proof-3145-a.md`, because B's failing test is added and deleted inside
the branch.

### 1. A live refresh: OBSERVED

Sweep 1, [37080078687](https://github.com/dreadnought-foundry/bureau-harness/actions/runs/37080078687)
(`workflow_dispatch`, dispatched 16:59:15, finished 17:00:33, success). The log
line, verbatim, at `2026-10-03T00:00:26Z`:

```
stale-merge-ref: PR #3100 f4f066ef — test red on this head and on merge base fc09c2ad, green on `main` 9596d756 (1 commit(s) ahead) — the fault was main-side and `main` has fixed it; refreshing the merge ref onto `main` 9596d756
```

* **The write.** `update-branch` made head
  `f9a53f6f478cdfd3eddd04dcfe7c25e0be4b9e87` ("Merge branch 'main' into
  agent/proof3145-main-fault", author `agent-bureau-bot[bot]`, parents
  `f4f066ef…` and `9596d756…`), at 16:59:59.
* **The receipt on the pull request.** It was posted by `agent-bureau-bot[bot]`
  at 17:00:01. Its first line is
  `stale-merge-ref-refresh @9596d756df34fdebd841f6c9f8d3d85156f1b839` and its
  trailer is
  `📎 pipeline-act: merge-ref-refreshed · kind: recovery · state: dispatched · next: qa-review.yml · discharges: nothing · subscriber: qa-review.yml · tag: stale-merge-ref-refresh`.
  It reads "1/3 refreshes used on this pull request".
* **Head sha before and after.** Before: `f4f066ef33c7a97093a0aaf5069f7b7f1105f979`.
  After: `f9a53f6f478cdfd3eddd04dcfe7c25e0be4b9e87`.
* **The `main` sha the receipt names.** `9596d756df34fdebd841f6c9f8d3d85156f1b839`
  (M2).
* **CI on the new head.** The refresh's push came from the App, so it fired the
  `synchronize` events, as the docstring says it must. CI
  [37080139547](https://github.com/dreadnought-foundry/bureau-harness/actions/runs/37080139547)
  finished at 17:00:54 with `test` **success**.
* **The card receipt: not applicable on the sandbox.** Fixture branches carry
  no `DRE-n`, so `branch_card()` is None and no card comment is made. That is
  by design: the harness writes nothing to Linear. The card-side half of
  criterion 1 is therefore not shown here.

### 2. No second refresh: OBSERVED

Sweep 2, [37080264618](https://github.com/dreadnought-foundry/bureau-harness/actions/runs/37080264618)
(`workflow_dispatch`, dispatched 17:01:37, finished 17:02:26, success). `main`
was still `9596d756`, and #3100 was open at head `f9a53f6f`. The log line,
verbatim, at `2026-10-03T00:02:18Z`:

```
stale-merge-ref: PR #3100 — already-refreshed: already refreshed onto `main` 9596d756 — at most one refresh per `main` commit
```

#3100 kept exactly one `stale-merge-ref-refresh` comment. Its thread across
the window was the critic's APPROVE, the one receipt, the gate's
carried-verdict note and the gate's hold note. No scheduled sweep ran between
the two dispatched ones. The last scheduled sweep before them,
[37079139882](https://github.com/dreadnought-foundry/bureau-harness/actions/runs/37079139882),
ran at 16:46, before either fixture existed.

### 3. A pull request with its own failure left alone: OBSERVED

#3099 was red on `test`, green on its merge base M0, and `behind_by` 2. Both
sweeps printed the same line. In sweep 1 it was at `2026-10-03T00:00:26Z`, and
in sweep 2 at `2026-10-03T00:02:18Z`:

```
stale-merge-ref: PR #3099 — own: test is green on the merge base — this pull request's own defect, and the fix loop owns it
```

No `update-branch` was made and no receipt was posted. #3099's head stayed
`81f92aaa…` throughout, and its only comment is the critic's REQUEST_CHANGES.

### 4. Did the critic verdict carry: YES, and no sibling card is needed

QA Review [37080140026](https://github.com/dreadnought-foundry/bureau-harness/actions/runs/37080140026)
ran on the refreshed head `f9a53f6f`. `should_review_pr.py` decided, verbatim:

```
review=false
carried_sha=f4f066ef33c7a97093a0aaf5069f7b7f1105f979
skipping 'agent/proof3145-main-fault' — the standing APPROVE for f4f066ef33c7a97093a0aaf5069f7b7f1105f979 still binds this head's content; re-reviewing would re-read an unchanged diff (DRE-2340)
```

No fresh review ran on the unchanged diff, so there is no finding against the
carry and no sibling card. The merge gate agreed. Merge Gate
[37080214676](https://github.com/dreadnought-foundry/bureau-harness/actions/runs/37080214676)
(17:01:15) posted "♻️ Merge gate: carried verdict content:7ebcb25a…", then
decided:

```
stacked_prs: 3 open pull request(s); stacked under #3100: #3099
decision=hold
reason=this branch also carries other open pull requests, and merging it would land their work in the base unapproved: #3099 — the critic asked for changes (REQUEST_CHANGES). Holding until each of them is approved or has merged on its own (DRE-4103)
```

So the gate would have merged on the carried verdict, and only the fixture's
deliberate stack stopped it.

### 5. What the console shows: NOT APPLICABLE on the sandbox

The console reads the card, and a sandbox pull request has no card. The
2026-09-10 reading below, of the act row and registry, still stands. A
screenshot of a live receipt on a live card is still owed, and only a
production refresh can supply it.

### Put back

Both pull requests were closed unmerged, A first so that closing B could not
lift A's stack hold. #3100 was closed at 17:02:54 and #3099 at 17:02:57, and
both branches were deleted. Harness `main` is
`9596d756df34fdebd841f6c9f8d3d85156f1b839`, with `test` green and a tree
identical to M0's.

### Where this leaves the card, as of 2026-10-02

| Acceptance criterion | State |
| --- | --- |
| One refresh observed, with PR receipt and card receipt | **Met for the PR** on the sandbox (#3100, sweep 37080078687). The card receipt is not applicable there, by design |
| The same pull request not refreshed again on the next sweep | **Met** (sweep 37080264618, `already-refreshed`) |
| A pull request with its own failure left alone, log line quoted | **Met** (#3099 on both sweeps, plus the 2026-09-10 production cases) |
| Whether the critic verdict carried | **Met**: it carried (`review=false`, `carried_sha=f4f066ef…`). No sibling card is needed |
| What the live console showed | **Partly met**: the act row is declared and shipped (2026-09-10). A live screenshot needs a production refresh on a carded PR |
| `docs/stale-merge-ref-proof.md` merged to `main` | This document |

---

## The first reading (2026-09-10), kept as written

Everything from here to the end is the first reading, made on production before any refresh had happened. It is unchanged.

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
| Rule reached bureau-pipeline `main` | `763f0e9c` — merge of PR #303, 2026-09-07 16:14 PT |
| bureau-pipeline's own sweep runs | `.github/workflows/self-reconcile.yml`, `reconcile.yml@main`, `pipeline_ref: main` |
| agent-bureau's sweep runs | `.github/workflows/reconcile.yml`, `reconcile.yml@stable`, `pipeline_ref: stable` |
| `stable` at the time of reading | `b860c24144b66e918117a09d17e7bcac08cfcf7d`, which contains `763f0e9c` |
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
