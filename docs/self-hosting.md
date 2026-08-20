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

Mechanics of pinning, the canary channel, and the promotion/rollback moves
live in the README under "Release channel: pinning, canary, promotion".
