# Integration harness (DRE-2098)

End-to-end scenarios against the dedicated sandbox repo
**`dreadnought-foundry/bureau-harness`**, driven by
`.github/workflows/harness.yml` (workflow_dispatch, job id `harness`,
input `pipeline_ref` default `main`). The scenarios mock **nothing
GitHub-side** — real branches, real PRs, the sandbox's real critic and
merge-gate stubs, real App identities. Unit tests
(`tests/test_harness_*.py`) cover only the driver's pure logic.

## Layout

| file | role |
| --- | --- |
| `framework.py` | phases, run-id namespacing, leftover sweep, verdict analysis (reuses `merge_gate.py`'s parsing) |
| `github_api.py` | stdlib REST client — one client per bot identity |
| `scenarios/` | one module per scenario, discovered by convention (`SCENARIO` export); siblings add files, never edit a registry |
| `__main__.py` | CLI: `PYTHONPATH=scripts python3 -m harness --scenarios bot_pr_flow` |

## Namespacing and self-cleaning

Every branch a run creates is `agent/harness-<run-id>-<scenario>` and
every merged probe file lives under `harness_runs/`. Setup sweeps ALL
leftovers matching that namespace (any run id) — closing PRs, deleting
branches, removing stray probe files — so a crashed previous run can
never fail the next one. The sweep predicate can never match a real
`agent/DRE-n-*` branch (unit-pinned). Cleanup always runs, and a cleanup
failure fails the scenario: leaving the sandbox unusable IS a failure.
`harness.yml` holds a no-cancel concurrency group, so two runs never
share the sandbox.

## The Linear side (decided per the card)

Harness branches carry **no `DRE-n` card reference**. `should_review_pr.py`
reviews them via the `agent/` prefix alone, and every Linear touchpoint
then no-ops deterministically:

* qa-review posts its card comment only when the branch yields a card ref — none here;
* linear-sync extracts its card from the head branch — none, so no card transitions;
* reconcile sweeps Linear cards, none of which reference harness PRs.

No permanent harness card, no sandbox Linear stubs, zero Linear writes —
the harness cannot spam real cards because it never addresses one.

## What the sandbox must provide (operator card DRE-2097)

* Both Apps (worker bot + qa-bot) installed on `bureau-harness`; this
  repo's `BUREAU_APP_*` / `BUREAU_QA_APP_*` secrets mint sandbox-scoped
  tokens.
* The pipeline stubs (qa-review on `pull_request`, merge-gate on its
  `workflow_run` list) with the sandbox's own secrets, plus at least one
  CI workflow that reports a check run on PR heads — the merge gate
  fail-closes to `wait` when no non-review checks exist.

`pipeline_ref` pins the **driver** checkout. Which pipeline ref the
sandbox's stubs consume is pinned in the sandbox's own stub files — the
wiring card's business.

## Scenario `bot_pr_flow`

Worker bot pushes the namespaced branch and opens a cardless, docs-only
probe PR (one markdown file — nothing a sandbox CI could collect or
accumulate). Verify waits for a qa-authored verdict comment **bound to
the head sha** (`VERDICT: … @<sha>`, parsed with the gate's own code) and
fails fast on a bound REQUEST_CHANGES; then waits for the merge and
asserts the merger is the qa-bot (author ≠ merger, `[bot]`-suffix
tolerant). Cleanup deletes the branch and the merged probe file
(best-effort on protected defaults) and asserts the default branch is
readable with no harness PRs left open.
