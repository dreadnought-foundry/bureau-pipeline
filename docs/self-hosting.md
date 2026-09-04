# Self-hosting: bureau-pipeline on its own rail

The go-live record for DRE-1929 Option A ("agents author, human promotes,
**harness proves**" — ADR `adr-bureau-pipeline-self-host.md` in the
agent-bureau repo; the third clause added by DRE-2103).

## The facts

- **bureau-pipeline became a dispatch target on 2026-07-11.** Cards labeled
  `repo:bureau-pipeline` now ride the same rail as any product repo: the
  relay dispatches here, the `self-*` stubs (merged in PR #74) call this
  repo's own reusable workflows, and the normal build → PR → critic →
  merge-gate flow applies.
- **The fleet consumes tagged releases.** v1 = `7ff9374` (the commit the
  operator cut the annotated `v1` tag at). Product repos pin `@v1` with a
  matching `pipeline_ref: v1`.
- **agent-bureau and bureau-pipeline itself ride `@main`** — the canary
  channel. Every merge to main soaks on the canaries' real traffic before
  the fleet sees it.
- **A merge to main here changes nothing live for the fleet — it only
  stages the NEXT release.** The human gate is release promotion: the
  operator cuts or re-points the `vN` tag at the soaked sha. Agents author;
  a human promotes. **DRE-2551 did not change this** — it added a second,
  automatic ref that nothing pins yet; see the last fact below.
- **The harness proves every release (DRE-2103).** The operator cuts `vN`
  only after a green integration-harness run against the candidate sha —
  the `pipeline_ref` input on `harness.yml` is how a candidate is tested
  pre-tag:

  ```bash
  gh workflow run harness.yml --repo dreadnought-foundry/bureau-pipeline \
    -f pipeline_ref=<candidate-sha>
  ```

  A green run stamps a success `integration-harness` commit status on the
  tested sha; `release-gate.yml` fires on every `v*` tag push (and, since
  DRE-2551, on `stable` — see below) and goes
  loudly red when the tagged commit lacks that stamp
  (`scripts/release_gate.py`, fail-closed). The harness also gates
  boundary-touching PRs here via its `pull_request` trigger — the merge
  gate's all-checks-green rule holds a PR whose harness run is red, and
  since DRE-2551 it runs on every push to `main` as well, so trunk commits
  carry a stamp of their own.
- **`stable` moves itself; `vN` is still cut by hand (DRE-2551).**
  `promote-channel.yml` keeps one moving tag, `stable`, on the newest
  commit on `main` carrying a green `integration-harness` stamp. No
  operator is involved, the repository variable `CHANNEL_HOLD` pauses it
  (unset means run), and `release-gate.yml` fires on `stable` too, so an
  automatic move is validated exactly like a hand-cut tag. Two limits are
  the point of this entry:

  - **Nothing consumes it.** No product repo pins `@stable`; this change
    re-pointed no stub. The fleet is on `vN` with a matching
    `pipeline_ref: vN`, unchanged.
  - **The `vN` flow stayed manual and operator-only** — cutting or
    re-pointing the tag, then re-pointing the stubs, exactly as the README
    describes. That is still the human gate between a merge here and
    anything the fleet runs — the "agents author; a human promotes" fact
    above holds in full.

  - **A quiet channel now says so (DRE-2552).** `channel-watch.yml` runs
    daily and raises one deduplicated Linear card when `stable` has stopped
    advancing while `main` moves — 8+ commits behind for 72h, or anything
    unpromoted for 14 days, thresholds derived from this repo's measured
    commit cadence (README, "Channel staleness alarm"). A **held** channel
    is reported as held rather than as broken, so set `CHANNEL_HOLD` to
    `who=<name> since=<ISO date> <why>`: GitHub will not tell the watcher
    who set a variable or when, and a hold nobody owns is reported as
    exactly that. The watcher holds `contents: read` and cannot move the
    ref it watches.

  So what `stable` buys today is the pre-tag question above, answered
  continuously instead of by a hand-run dispatch: it is always the newest
  proven sha on `main`, so once a candidate has soaked on the canary the
  operator has its proof already in hand. Choosing WHICH sha to release,
  and cutting it, stays theirs. Whether the
  fleet ever pins `stable` directly — retiring the manual cut — is **not
  decided by DRE-2551** and is not in its scope. Until that decision is
  made and written here, `vN` is the only fleet-facing channel.

## Queue behind, never cancel (DRE-3070)

`stable` advances only for a commit the harness PROVED, so the channel moves
at the speed the harness finishes — and one sandbox repo means one harness run
at a time. On a busy evening that is a queue, and how the queue behaves is the
difference between a channel that lags and a channel that stops.

**The rule: `harness.yml` holds `cancel-in-progress: false`, and it is
load-bearing for the channel, not just for the sandbox.** The run proving
commit N is allowed to finish and promote; the newest head queues behind it.

**Why `cancel-in-progress: true` would be exactly wrong here.** It reads like
the efficient choice — why prove a commit nobody is on any more? — and it
freezes the channel outright. Every merge would kill the run proving the merge
before it, so on the nights with the most changes waiting, no run would ever
finish and `stable` would never advance. The mechanism built to keep the fleet
off an unproven commit would instead hold it on a stale one, indefinitely.

**What the rule costs, and why that cost is accepted.** GitHub keeps at most
ONE pending run per concurrency group, so a head still waiting when the next
push arrives is cancelled *before it starts*. Intermediate heads are therefore
skipped: the channel advances to N, then to the latest, rather than to every
commit in between. That is the trade — a skipped head, never a skipped
channel. In the run list those skips look alarming and are not: a displaced run
has `run_started_at == created_at`, because it never ran.

**The residual gap, written down rather than papered over.** The group is one
constant shared by the `push` and `pull_request` triggers, because it guards a
single sandbox repo whose leftover-sweep would delete a concurrent run's
branches. So a PR harness run and the trunk's proving run compete for the same
pending slot, and the trunk's can lose (2026-09-03, run `33832750432`,
`main@46ca2476`, displaced by the DRE-3059 PR run). Closing that needs a real
lock on the sandbox, not a second concurrency group; until then the condition
is reported rather than prevented.

### Reading a channel that did not move

Every completed harness run — on `main` or on a PR head — now leaves a
promote-channel receipt naming one outcome, so "the channel is quiet", "the
channel is starved" and "that run was never about the channel" are different
strings instead of the same silence:

| receipt | what happened | what to do |
| --- | --- | --- |
| `harness-passed-promoting` | green run, strictly ahead — `stable` moved | nothing |
| `harness-run-not-on-main` | a PR-head run; it proved a commit that is not on the trunk | nothing — it was never a candidate |
| `harness-cancelled-by-newer-push` | displaced by a merge train; never started | nothing — it advances when the trunk quietens |
| `harness-failed` | the harness went red on this commit | a red trunk; the medic and `red-main-repair.yml` own it |
| `channel-held` | `CHANNEL_HOLD` is set | clear the variable when the hold is done |
| `harness-blocked-by-sandbox` | the harness never judged this commit — its own sandbox (reconcile/merge-gate/linear-sync) failed first | nothing proven either way; the next run re-proves this trunk |
| `no-harness-stamp` | no green `integration-harness` status on this sha | fail-closed by design; check the harness run |
| `not-ahead-of-channel` | already there, or behind | nothing — the channel never moves backwards |

Before this, those runs concluded `skipped` with nothing else on them: on
2026-09-03 four consecutive PR-head runs each produced one, and learning that
nothing was wrong meant opening all four.

`channel-watch.yml` counts the `cancelled` harness runs on `main` since the
channel head and, at two or more, names a **merge train** in the staleness
alarm instead of reporting the cause as unknown — and says whether a run is
proving main right now, because a train with a run working on it needs nothing
and a train that has stopped with the trunk still unproven does. One skipped
head is the rule above working; two is merges arriving faster than the harness
can prove them. The lever is the harness's duration or the merge rate — never
cancelling the run in progress.

### How long a run is allowed to take

The other half of a starved channel is a single run that will not end. Every
harness run now writes `⏱ harness scenarios: Nm (budget 40m)` to its summary
and raises a GitHub warning past the budget. Healthy runs measure 9–18 minutes;
the budget sits well above that and well below the job's 180-minute timeout,
which is a ceiling for a bad day rather than a budget — past it the run is
*cancelled*, and a cancelled run stamps nothing at all.

The number exists because on 2026-09-03 a main run sat on `Run harness
scenarios` for 54 minutes: the sandbox's Linear quota had been exhausted and
the scenario was waiting on a sweep that would never come. Nothing
distinguished that from a long queue until an operator read the logs and
cancelled it by hand. **The warning does not shorten the wait** — a scenario's
own wait still has no deadline shorter than the job timeout, and the harness
does not yet fail fast when the sandbox itself has died.

Mechanics of pinning, the canary channel, and the promotion/rollback moves
live in the README under "Release channel: pinning, canary, promotion".
