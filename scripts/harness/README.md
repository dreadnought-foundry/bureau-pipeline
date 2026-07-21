# Integration harness (DRE-2098, scenarios 2-3 DRE-2100)

End-to-end scenarios against the dedicated sandbox repo
**`dreadnought-foundry/bureau-harness`**, driven by
`.github/workflows/harness.yml` (job id `harness`; workflow_dispatch with
input `pipeline_ref` default `main`, plus a `pull_request` trigger on the
boundary paths — DRE-2103). The scenarios mock **nothing GitHub-side** —
real branches, real PRs, the sandbox's real critic and merge-gate stubs,
real App identities. Unit tests (`tests/test_harness_*.py`) cover only
the driver's pure logic.

The harness is **load-bearing** (DRE-2103): on a boundary-touching PR the
driver runs from the PR's own head and its red check run holds the merge
gate (all-checks-green, no branch-protection change); dependabot-triggered
PR events self-skip clean at the job level (empty Dependabot secrets
store). A green run stamps a success `integration-harness` commit status
on the tested sha — the record `release-gate.yml` requires before any
`v*` tag stands ("agents author, human promotes, harness proves").

## Layout

| file | role |
| --- | --- |
| `framework.py` | phases, run-id namespacing, leftover sweep, verdict analysis (reuses `merge_gate.py`'s parsing) |
| `github_api.py` | stdlib REST client — one client per bot identity |
| `scenarios/` | one module per scenario, discovered by convention (`SCENARIO` export); siblings add files, never edit a registry |
| `__main__.py` | CLI: `PYTHONPATH=scripts python3 -m harness --scenarios bot_pr_flow` |

## Namespacing and self-cleaning

Every branch a run creates is `agent/harness-<run-id>-<scenario>` (or
`dependabot/harness-<run-id>-…` for gate_paths' condition-D probe) and
every merged probe file lives under `harness-runs/` — deliberately NOT a
Python identifier, so setuptools flat-layout auto-discovery in the
sandbox can never claim it as a second top-level package (the old
`harness_runs` name broke the sandbox's own `pip install -e .`, held its
CI red on every probe PR, and the gate rightly never merged one — run
29795108949; the legacy dir is still swept). Setup sweeps ALL
leftovers matching those namespaces (any run id) — closing PRs, deleting
branches, removing stray probe files — so a crashed previous run can
never fail the next one. The sweep predicate can never match a real
`agent/DRE-n-*` branch or a genuine `dependabot/<ecosystem>/…` branch
(unit-pinned). Cleanup always runs, and a cleanup failure fails the
scenario: leaving the sandbox unusable IS a failure. `harness.yml` holds
a no-cancel concurrency group, so two runs never share the sandbox.

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
  tokens (the qa token is also threaded to the driver as
  `HARNESS_QA_TOKEN` — the proven reader for commit check-runs,
  merge-gate.yml's own path).
* The pipeline stubs (qa-review on `pull_request`, merge-gate on its
  `workflow_run` list) with the sandbox's own secrets, plus at least one
  CI workflow that reports a check run on PR heads — the merge gate
  fail-closes to `wait` when no non-review checks exist.
* For `dependabot_flow` (DRE-2100): a reconcile stub on its ~15-min cron
  (the workflow_dispatch review route under test) and a stale pinned
  dependency that keeps a genuine Dependabot PR filed — chosen
  major-stale so the gate parks it and the fixture persists between
  runs. `@dependabot rebase` / `@dependabot recreate` comments
  regenerate activity when needed.

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

## Scenario `dependabot_flow` (DRE-2100)

Consumes the sandbox's REAL open Dependabot PR (never closes it — it is
the vendor's standing fixture): asserts the dependabot-actor
`pull_request` review run self-skipped clean (a `skipped` check run on
the head, never a red crash — DRE-2067), waits for the reconcile
dispatch route's sha-bound verdict (budgeting one cron interval —
DRE-2047/2053), and audits the receipt lifecycle (1..cap worker-bot
receipts per head; past-cap = looping sweep; zero receipts on an
untouched head = the receipted route was bypassed — DRE-2049/2071).
Coverage limits (no conjurable Dependabot PR, no on-demand critic crash,
App-bot `@dependabot` commands not vendor-guaranteed) are documented in
the scenario module itself.

## Scenario `gate_paths` (DRE-2100)

Merge-gate semantics with three synthesized PRs: a behind-base probe
(the gate must update-branch **as the qa-bot** and the synchronize
actor must pass the review allowlists — no lockout, then a normal
merge); a worker-authored `dependabot/harness-…` probe (condition D →
the waiting-for-human state posted exactly once, PR never touched); and
a stale-verdict race (a push right after a bound APPROVE must hold the
merge until a fresh verdict binds the new head). When the real
Dependabot PR is observable, its gate arm (major → human/untouched,
provable minor/patch + APPROVE → qa-bot auto-merge) is asserted too.
