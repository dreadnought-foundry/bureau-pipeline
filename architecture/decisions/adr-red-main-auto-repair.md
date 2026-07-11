# ADR: red-main auto-repair — the build fixes itself instead of a human

- **Status:** Proposed — awaiting CEO sign-off on DRE-1925. The build card
  ([DRE-1923](https://linear.app/dreadnoughtfoundry/issue/DRE-1923)) is
  blocked until this ADR is Accepted; sign-off is captured on the DRE-1925
  card (the CEO's comment/approval there flips this line to Accepted).
- **Date:** 2026-07-11
- **Cards:** DRE-1925 (this design), DRE-1923 (the build it gates)

## Context

When `main` CI goes red in a product repo, every agent branched after that
point builds on a broken base, the merge gate (rightly) stops merging, and
the fleet stalls until a human notices. The pipeline already self-heals
individual *runs* (the medic retries transient flakes once, then diagnoses)
and individual *PRs* (agent-fix answers REQUEST_CHANGES and conflicts) — but
nothing forward-fixes a red `main` itself. That gap is currently a human's
pager.

An agent that edits `main` to go green is **sensitive**: the obvious failure
modes are an agent that guts tests to make them pass, a repair loop that
burns quota re-fighting an unfixable failure, two repairs clobbering each
other, and repair traffic starving normal card dispatch. Each gets a
concrete mechanism below — that is the point of this ADR existing before any
build.

## Decision — trigger

Event-driven off the existing `workflow_run` rail, exactly like the medic
and the merge gate: the repair workflow fires on the product repo's CI
workflow completing with `conclusion == 'failure'` AND
`head_branch == <default branch>`. **No new polling loop** is added — the
reconcile sweep stays the only scheduled job in the system, and it gains no
repair duties.

A failure on any non-default branch is out of scope: branch CI failures
already route through agent-fix (critic rejections) and the medic (run
crashes).

## Decision — fix flow

1. **Classify first, dispatch second.** Before any agent spins up, the
   workflow pulls the failed run's logs (`gh run view --log-failed`, the
   medic's exact pattern) and classifies them with
   `scripts/medic_classify.py`-style rules — see guardrail 2. Only a *real
   code/test failure* proceeds; infra noise backs off.
2. **Scoped fix agent, forward-fix only.** The repair agent receives the
   failing job logs and the failing head SHA as context. It fixes *forward*
   on a branch (`repair/<failing-sha>`): never a push to `main`, never a
   force-push, never a blind revert. If the offending commit should be
   reverted, the revert itself ships as a reviewed PR like any other change.
3. **A normal PR through the normal gates.** The repair PR is authored by
   the worker identity (`agent-bureau-bot`), reviewed by the adversarial
   critic (qa-review), and merged only by the qa identity
   (`agent-bureau-qa-bot`) on CI green + verdict APPROVE. Author ≠ merger is
   enforced by different GitHub App identities, not policy — the repair
   agent structurally cannot merge its own fix.
4. **Escalate when unsure.** If the agent cannot confidently produce a fix
   (ambiguous cause, a product-behavior choice, a destructive-looking
   remedy), it opens no PR and posts a plain-English question to Linear —
   the card parks in **Plan Review**, the existing "needs you" lane. A
   confused repair agent asking beats a confident one guessing on `main`.

## Guardrail 1 — no test-gutting

The #1 danger: an agent that makes CI green by weakening or deleting the
tests that caught the breakage. Mechanisms, layered:

- **The critic reviews every repair PR** — repair gets no review bypass of
  any kind. The qa-review context for a repair PR additionally includes the
  ORIGINAL failing job log, so the critic judges "does this diff fix what
  actually failed" — not just "is this diff plausible."
- **Test-touching diffs are mechanically flagged.** Repair PRs are
  identifiable by branch prefix (`repair/`); a deterministic path check (the
  `check_tdd_commits.py` classifier pattern) marks any repair diff that
  edits or deletes files under the repo's test tree, and that flag is
  surfaced into the critic's context as a mandatory finding to resolve.
- **The stale-test rule.** The fix agent's brief requires it to classify
  each failure as "test is stale → update the test" vs "code is broken →
  fix the code," and to state that claim in the PR body with the log
  evidence. The critic verifies the claim against the same log. Any diff
  that weakens an assertion, loosens a tolerance, adds a skip/xfail, or
  deletes a test WITHOUT a verified stale-test justification earns
  **REQUEST_CHANGES** — the critic's standing instruction for repair PRs is
  that going green by silencing the signal is the failure mode it exists to
  catch.
- **Default posture: code-only diffs.** A forward-fix that touches zero test
  files is the expected shape; anything else is the exception that must
  justify itself.

## Guardrail 2 — no crash-loop

We lived the 2026-06-28 medic↔critic quota crash-loop (six PRs stuck,
the bot's GitHub quota burned twice). Repair must not rebuild it:

- **Classify before acting.** The same discipline `scripts/medic_classify.py`
  encodes (DRE-1921) applies at the repair trigger: a failure whose logs
  carry an infra fingerprint — rate-limit signatures, auth/startup death,
  runner flake — is NOT a code failure a fix agent can fix. On infra: the
  repair workflow **backs off** entirely (no agent, no retry — the medic
  already owns the retry-once for transient flakes; on a rate-limit, the
  window resetting is the fix).
- **Bounded attempts, keyed by the failing SHA.** At most **2** repair
  attempts per distinct failing head SHA on `main`, tracked mechanically
  (the `repair/<failing-sha>` branch and its PR are the attempt record — no
  external state). Budget exhausted → stop and raise a plain-English Linear
  triage card for a human; never a third swing at the same wall.
- **Repair never watches itself.** The trigger is the product repo's CI on
  the default branch only — a repair run's own failure routes through the
  existing medic, and a repair PR's review rejections route through the
  existing agent-fix loop with its existing budgets (3 review-fix attempts,
  5 conflict rounds, then human hold). No new retry loop is introduced
  anywhere.

## Guardrail 3 — concurrency lock

One repair in flight per repo (the DRE-1803 concurrent-clobber lesson —
two agents converging on the same target destroy each other's work):

- **Actions `concurrency` group** `red-main-repair-<repo>` with
  `cancel-in-progress: false` serializes the trigger itself.
- **Liveness check before dispatch** (the DRE-2032 pattern): if an open
  `repair/*` PR or a live repair run already exists for this repo, a new
  failure event is a **no-op** — the in-flight repair's merge will re-run
  CI on `main` and either clear the newer failure or produce a fresh event
  to handle next.
- **Debounce by SHA:** multiple failing `workflow_run` events off the same
  failing head SHA (matrix jobs, re-runs) collapse into one repair — the
  `repair/<failing-sha>` branch already existing makes the duplicate event
  a no-op.

## Guardrail 4 — quota isolation

Repair traffic must never starve card dispatch (the same 5,000 req/hr
installation buckets the 2026-06-28 incident exhausted):

- **Mint through the dispatch pool.** The repair worker token is selected by
  `scripts/dispatch_pool.py` (DRE-2013) with pool key `repair:<failing-sha>`
  — the selector's max-remaining pick steers repair onto the bucket with the
  most headroom, away from whichever bucket card dispatch is currently
  draining.
- **Bounded by construction.** Guardrails 2 and 3 cap total repair traffic
  at one in-flight repair per repo with ≤2 attempts per failure — repair
  cannot generate the unbounded call volume that starvation requires. If
  measured contention appears anyway, the escalation path is a dedicated
  pool slot for repair (a fifth App is $0), not a bigger share of the shared
  buckets.

## Alternatives considered

- **Auto-revert the offending commit directly on `main`.** Rejected: a
  direct push bypasses the critic and the two-robot split, and a blind
  revert can destroy a mostly-good merge. Forward-fix through a reviewed PR
  keeps every safety property we already trust; a revert is still available
  *as* that PR when it is the right fix.
- **A polling sweep for red `main`.** Rejected: the `workflow_run` event
  already exists, fires immediately, and adds zero scheduled load. Polling
  is how the 2026-06-28 class of loop gets rebuilt.
- **Let the medic grow this.** Rejected: the medic's contract is
  run-level triage (retry/diagnose), deliberately code-blind. Writing a fix
  is an engineer-agent job with engineer-agent gates.

## Consequences

- A red `main` gets a candidate fix PR within one agent-run of the failure,
  with a human needed only when the agent escalates or the attempt budget
  exhausts.
- The critic's workload grows by one PR per `main` breakage — the price of
  never merging an unreviewed repair.
- The build card (DRE-1923) implements exactly the mechanisms named here;
  divergence requires updating this ADR first (the pin-test
  `tests/test_adr_red_main_auto_repair.py` holds the two in lockstep).

## Sign-off

CEO sign-off is captured on DRE-1925 in Linear. Until then this ADR is
**Proposed** and DRE-1923 must not start; on sign-off, flip the Status line
to Accepted and unblock the build card.
