# Self-hosting — bureau-pipeline on its own rail

The go-live record for [DRE-1929](https://linear.app/dreadnoughtfoundry/issue/DRE-1929)
Option A ("agents author, human promotes"; ADR `adr-bureau-pipeline-self-host.md`
in agent-bureau).

- **bureau-pipeline became a dispatch target on 2026-07-11.** Cards labeled
  `repo:bureau-pipeline` ride the same rail as any product repo: the thin
  `self-*.yml` stubs (merged in PR #74) call this repo's own reusable
  workflows at `@main`.
- **The fleet consumes tagged releases** — v1 is the annotated tag at commit
  `7ff9374` (`git rev-parse v1^{commit}`). agent-bureau and bureau-pipeline
  itself ride `@main` as the canary channel.
- **Merges to main here stage the NEXT release.** The human gate is release
  promotion: the operator cuts or re-points the vN tag; nothing the agents
  merge reaches the tagged fleet on its own.
- Pinning mechanics, canary policy, and the promotion/rollback commands live
  in the README's "Release channel: pinning, canary, promotion" section.
