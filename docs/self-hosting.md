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
  a human promotes.
- **The harness proves every release (DRE-2103).** The operator cuts `vN`
  only after a green integration-harness run against the candidate sha —
  the `pipeline_ref` input on `harness.yml` is how a candidate is tested
  pre-tag:

  ```bash
  gh workflow run harness.yml --repo dreadnought-foundry/bureau-pipeline \
    -f pipeline_ref=<candidate-sha>
  ```

  A green run stamps a success `integration-harness` commit status on the
  tested sha; `release-gate.yml` fires on every `v*` tag push and goes
  loudly red when the tagged commit lacks that stamp
  (`scripts/release_gate.py`, fail-closed). The harness also gates
  boundary-touching PRs here via its `pull_request` trigger — the merge
  gate's all-checks-green rule holds a PR whose harness run is red.

Mechanics of pinning, the canary channel, and the promotion/rollback moves
live in the README under "Release channel: pinning, canary, promotion".
