# Self-hosting: bureau-pipeline on its own rail

The go-live record for DRE-1929 Option A ("agents author, human promotes" —
ADR `adr-bureau-pipeline-self-host.md` in the agent-bureau repo).

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

Mechanics of pinning, the canary channel, and the promotion/rollback moves
live in the README under "Release channel: pinning, canary, promotion".
