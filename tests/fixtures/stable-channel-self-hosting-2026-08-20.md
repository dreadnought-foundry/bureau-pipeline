<!-- docs/self-hosting.md, verbatim at af3f7b05 (2026-08-20): the channel entry as it read before
     DRE-2553 moved the fleet onto `@stable` on 2026-08-23. Pinned for
     tests/test_self_hosting_docs.py (DRE-2709) — the stable-channel guard must
     FAIL on this text. Do not edit it to match today's docs. -->

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
