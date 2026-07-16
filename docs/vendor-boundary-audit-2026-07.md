# Vendor-boundary backfill audit — 2026-07

One-time backfill sweep (DRE-2110, filed under epic DRE-2073): the five-question
premortem checklist from `standards/vendor-boundaries.md` (DRE-2105), applied to
every EXISTING external-boundary surface in this repo. The rollout incidents
were found live, one at a time; this audit walks the whole surface once, on
paper, as of **2026-07-16**.

The five questions, per the standard:

- **Q1** — who is the initiating actor, and do all reachable actor gates admit it?
- **Q2** — which secrets store does that event context get?
- **Q3** — what does the vendor actually do on retry / close / reopen / ignore /
  rebase / re-file, and does our state machine survive each?
- **Q4** — what are the command's limitations?
- **Q5** — what happens when OUR run crashes mid-flow (receipts, bounded retry,
  classify-first)?

**Verdict key:** `covered` — every question has a live, tested answer;
`GAP` — a real hole, filed as a Linear card and referenced by id.

**Gaps filed by this audit:**

| Card | Surface | Gap |
|---|---|---|
| DRE-2117 | merge-gate.yml | `gh pr merge` doesn't pin the evaluated head sha (`--match-head-commit`) — a rebase in the eval→merge window merges a head the critic never approved |
| DRE-2118 | @dependabot commands | no safe rejection path for parked majors — both known commands are booby-trapped (walk-down / grouped-unsupported), no config `ignore` playbook |
| DRE-2119 | dependabot.yml | parked majors accumulate against `open-pull-requests-limit: 5` and silently starve the minor/patch group — no WARN/CRITICAL |
| DRE-2120 | merge-gate.yml + agent-fix.yml | hardcoded `agent-bureau-qa-bot[bot]` job-if literals vs the derived app-slug — an App rename darkens the comment-triggered legs silently |

**Scope note:** the relay Lambda (HMAC verification, replay/stale-event drop,
repo routing) lives in the bureau-linear-relay repo and is out of scope here;
its boundary questions are noted where its dispatches land (agent-task, plan).

---

## agent-task.yml — the card build (reusable)

Triggered via stubs binding `repository_dispatch: [agent-execute]` — from the
relay Lambda (dispatch-bot App) or from reconcile's `redispatch()`
(`scripts/reconcile.py`, App token, needs `contents:write`).

**Q1 — actor.** A `repository_dispatch` run initiates as the App that sent it
(`agent-bureau-bot`). The Implement step's `allowed_bots:
agent-bureau-bot,agent-bureau-bot-2,agent-bureau-bot-3,agent-bureau-bot-4,github-actions`
(agent-task.yml:320) admits the dispatch bot, all pool workers (DRE-2020), and
`github-actions` (any `gh workflow run` path, DRE-2053).
`test_worker_pool_allowed_bots.py` pins the roster in every workflow.

**Q2 — secrets.** `repository_dispatch` only fires from an authenticated App
call — never a dependabot-actor context, so the empty Dependabot store
(DRE-2047) is unreachable on this surface. Pool secrets `BUREAU_APP_ID_2..4`
are optional; a missing slot shrinks the pool gracefully.

**Q3 — vendor behavior.** A replayed/duplicate dispatch is absorbed by the
duplicate-dispatch guard (see `dedupe_dispatch.py` section) plus the stub's
`concurrency: agent-<identifier>, cancel-in-progress: false`. Job re-runs
reuse the run id and never self-block.

**Q4 — command limitations.** The pool token mint has an explicit clause per
slot because Actions cannot index secrets dynamically; the `/rate_limit` probe
is quota-exempt. Both are encoded in `dispatch_pool.py` comments and its tests.

**Q5 — crash mid-flow.** The `🧠 model-attempt:` heartbeat carries the run URL,
so reconcile checks GitHub's real run status before declaring death (DRE-2032).
The always()-guarded Report step routes: PR → In QA; escalation → Plan Review;
blocker → Backlog (parked, never Todo — DRE-1286); silent death →
`dead_run.py decide` under the shared `dead-run-requeue` cap (2), with
`--cancelled` deferring to reconcile instead of burning the cap (DRE-2074) and
model-death markers swinging the model on retry.

**Verdict:** covered.

## plan.yml — epic planning (reusable)

Stub binds `repository_dispatch: [agent-plan]` (relay, on an `agent:planner`
card entering Todo).

**Q1 — actor.** Same dispatch-App actor as agent-task; `allowed_bots`
(plan.yml:193) carries the same pool + `github-actions` roster.

**Q2 — secrets.** Same as agent-task: App-authenticated dispatch only, no
dependabot-actor path. Single-App (no pool mint).

**Q3 — vendor behavior.** Route step distinguishes Triage (plan/re-plan) from
Todo (activate = `reconcile.py --promote-only`); re-delivery of the event
re-runs an idempotent path. Children are validated post-creation
(`validate_card.py check-children`, DRE-1715) and a bad child fails the run
loudly.

**Q4 — command limitations.** Child creation goes through
`linear_ops.py subissue`, which enforces label inheritance and rejects
path-shaped/empty bodies — no raw GraphQL from the agent.

**Q5 — crash mid-flow.** Plan death has no PR/requeue path: the medic re-runs
the SAME workflow_run (attempt 2), and the recorded `model-error:` marker
swings the model on the rerun. Epic parks in Plan Review only when children
exist; a question-only run parks in Backlog.

**Verdict:** covered.

## qa-review.yml — the adversarial critic (reusable)

Consumed by pr-review.yml here and fleet stubs on `pull_request` +
`workflow_dispatch`.

**Q1 — actor.** Job-if admits `workflow_dispatch` and PR events on
agent/repair/dependabot/DRE-carrying branches. `allowed_bots`
(qa-review.yml:380/517) admits the pool (DRE-2020), `agent-bureau-qa-bot`
(the merge gate's update-branch push fires `synchronize` as qa-bot, DRE-2037),
`dependabot` (its own opens/rebases, DRE-2039), and `github-actions`
(reconcile's `gh workflow run` dispatches, DRE-2053).

**Q2 — secrets.** The dependabot self-skip lives on the REUSABLE's job-if
leading conjunct (`github.actor != 'dependabot[bot]'`, qa-review.yml:82) —
the only point early enough for fleet stubs calling with `secrets: inherit`
(DRE-2067; DRE-2047 covered only this repo's stub). Dependabot PRs get their
real review via reconcile's `workflow_dispatch` (full secrets, PR ref — never
`pull_request_target`). Verdict quota draws from the qa-bot App's own bucket,
split from relay dispatch (2026-06-28 incident).

**Q3 — vendor behavior.** Verdicts are sha-bound (`VERDICT: <X> @<sha>`,
DRE-1990) with the sha captured BEFORE review starts — any push or dependabot
rebase demotes the verdict to stale and the gate waits for a fresh review.
Pre-DRE-1990 unbound verdicts are likewise treated as stale.

**Q4 — command limitations.** The two claude-code-action `with` blocks are
intentionally duplicated (Actions has no YAML anchors); the file says so and
requires keeping them in sync. Cardless dependabot PR bodies are sanitized,
size-capped, and fenced before prompt interpolation (DRE-2052/DRE-1996).

**Q5 — crash mid-flow.** Crash detection via `check_critic_result.py`
(is_error + verdict-file gate), one in-workflow retry, then a NEUTRAL
"QA Critic could not run" comment that supersedes any stale APPROVE without
rejecting (the DRE-1330 false-REQUEST_CHANGES lesson), posted with a 4×
backoff loop, then a loud red for medic visibility. Cancellation posts
nothing (a superseded run must not mask the live one).

**Verdict:** covered.

## pr-review.yml — this repo's own critic stub

`pull_request: [opened, reopened, synchronize]` + `workflow_dispatch`, calling
qa-review.yml@main fully qualified (a PR editing qa-review.yml cannot choose
the logic that reviews itself).

**Q1 — actor.** PR events fire as the pushing identity (pool bots, qa-bot's
update-branch push, dependabot) — all admitted downstream. `workflow_dispatch`
from reconcile initiates as `github-actions` — admitted.

**Q2 — secrets.** Dependabot-actor'd `pull_request` runs are skipped at the
stub job-if (pr-review.yml:52, DRE-2047) — belt to the reusable's suspenders.
`secrets: inherit` otherwise.

**Q3 — vendor behavior.** `concurrency: cancel-in-progress: true` means a
mid-review push cancels the stale review; qa-review's `!cancelled()` guards
keep a cancelled run from posting a fail-closed verdict.

**Q4 — command limitations.** `actions: read` is granted here because the
repair-PR context fetches failing-run logs with `github.token` (the qa-bot App
deliberately lacks it); absent, that stage degrades to maximum suspicion.

**Q5 — crash mid-flow.** Inherited from qa-review.yml (retry → neutral → red);
reconcile's In QA sweep re-dispatches reviews whose verdict isn't bound to the
current head.

**Verdict:** covered.

## verify.yml — the independent verifier (reusable)

Product-repo opt-in stub surface (`pull_request` + `workflow_dispatch`);
bureau-pipeline itself has NO verify stub — deliberate (no UI/multi-system
surface here), and merge_gate.py treats an absent verifier as a non-gate.

**Q1 — actor.** Job-if: dependabot-actor self-skip AND (`workflow_dispatch` OR
`agent/` head). `allowed_bots` (verify.yml:225/336) carries the full roster
including qa-bot (DRE-1924 skew guard), dependabot, and `github-actions`.

**Q2 — secrets.** Same leading-conjunct dependabot self-skip as qa-review
(DRE-2067). The qa-bot App pair is REQUIRED here (DRE-1987) so the verdict
comment is authored by qa-bot — the gate's trusted author — from its own quota
bucket.

**Q3 — vendor behavior.** PASS/FAIL verdicts sha-bound (`@<sha>`, DRE-1990),
captured pre-run; present-but-stale HOLDS at the gate (asymmetric vs the
critic: presence proves the PR is verifier-scoped).

**Q4 — command limitations.** Duplicated `with` blocks (no YAML anchors), same
keep-in-sync contract as qa-review.

**Q5 — crash mid-flow.** Two bounded attempts gated by
`check_critic_result.py`, neutral "QA Verifier" status on double-crash (holds
the gate without a false FAIL), comment retry loop, then loud red for the
medic. Out-of-scope PRs skip with no verdict and no block.

**Verdict:** covered.

## merge-gate.yml + scripts/merge_gate.py — the merge decision

Stub (self-merge-gate.yml and fleet equivalents) binds `workflow_run`
([Pipeline Tests, QA Review] completed), `issue_comment: [created]`, and
`workflow_dispatch` — both directions of the CI/verdict race.

**Q1 — actor.** `workflow_run` leg: gated to `workflow_run.event ==
'pull_request'` on agent/repair/dependabot heads; workflow_run events run with
full repo secrets regardless of the original run's actor (GitHub's documented
behavior — this is why the dependabot auto-merge rail works at all).
`issue_comment` leg: gated to `comment.user.login ==
'agent-bureau-qa-bot[bot]'` + body contains "QA Critic" — a dependabot or
human comment skips clean. **But** that login is a hardcoded literal (as is
agent-fix.yml's — see its section) while the decision step derives the trusted
login from the minted token's app-slug: an App rename darkens the
verdict-landing leg silently (merges then limp along on reconcile's ~15-min
gate nudges). Filed. The merge itself runs as the qa-bot App — author ≠ merger
by App identity, not policy.

**Q2 — secrets.** qa-bot App pair required; qa-bot deliberately lacks
`actions:read`, so the two Actions-API reads (verified-origin run listing, fix
dispatch) use `github.token` — each annotated fail-closed.

**Q3 — vendor behavior.** Verdicts are honored only sha-bound to the CURRENT
head (`merge_gate.py` condition 2): a dependabot rebase or any push demotes an
APPROVE to no-verdict and the gate waits. Stale branches are updated, never
merged blind (condition 0, DRE-1924); the update-branch push fires
`synchronize` as qa-bot — admitted by qa-review/verify allowlists (DRE-2037).
DIRTY dependabot PRs are never sent to the fix agent (Dependabot
rebases/recreates its own conflicts, DRE-2039). Closed PRs exit early;
reopened PRs re-evaluate statelessly. **The residual hole:** the final
`gh pr merge "$PR" --merge --delete-branch` (merge-gate.yml:249) does not pass
`--match-head-commit "$SHA"` — every condition was evaluated against a sha
captured at :164, and a rebase/push landing in that window merges a head no
verdict covered. Filed.

**Q4 — command limitations.** Dependabot policy (condition D): update-type
read from GitHub's own commit trailers, never the spoofable branch name;
grouped minor/patch proceed to the normal gates; majors and unprovable levels
→ `human` with a single idempotent ⏸️ note. REST check-runs API instead of
`gh pr checks` because the GraphQL rollup needs `actions:read`. In this repo
the dispatchable fix workflow is self-agent-fix.yml, not the reusable
(dispatching a workflow_call-only file 422s — DRE-2056).

**Q5 — crash mid-flow.** merge_gate.py is pure and stateless; every GitHub
read blip substitutes a fail-closed default (`{}` → wait). No receipts, so a
crashed evaluation leaves nothing that blocks the next event or reconcile's
nudge from re-evaluating from scratch.

**Verdict:** GAP — filed DRE-2117 (merge call doesn't pin the evaluated sha)
and DRE-2120 (hardcoded qa-bot login literal at the issue_comment job-if).

## agent-fix.yml — the fixing agent (reusable)

Stub binds `issue_comment: [created]` (critic REQUEST_CHANGES) and
`workflow_dispatch` (merge-gate conflict dispatch, reconcile's
approved-but-red / stuck-PR / dead-fix-run sweeps).

**Q1 — actor.** Job-if admits `workflow_dispatch` OR a PR comment authored by
`agent-bureau-qa-bot[bot]` containing `VERDICT: REQUEST_CHANGES` (DRE-1988).
`allowed_bots: "agent-bureau-qa-bot,github-actions"` is a deliberate security
lock — pinned by `AgentFixGateUnchangedTest`, never widened for the pool. The
qa-bot login here is the same hardcoded literal as merge-gate's (DRE-2120):
a rename strands REQUEST_CHANGES PRs with no sweep backstop.

**Q2 — secrets.** A dependabot[bot]-authored comment (it does comment — e.g.
the DRE-2062 "only available on single-dependency pull requests" reply) fires
the stub as dependabot actor with the empty store, but the reusable's job-if
excludes non-qa-bot authors before any mint runs, so the run skips clean.

**Q3 — vendor behavior.** Every counter reads only comments by their
legitimate authors (worker bot / qa-bot) — forged markers in PR comments are
invisible (DRE-1995). Convergence-halt markers are sha-bound so a real new
head re-arms cleanly.

**Q4 — command limitations.** Two separate bounded budgets: 3 review-fix
attempts (attempt 3 escalates the model), 5 conflict rounds; exhaustion posts
🛑 and parks for a human.

**Q5 — crash mid-flow.** The "dispatched" announcement posts BEFORE the agent
runs, which is why counters are author-filtered and push-bound. A fix run that
pushes nothing routes through `fix_dead_run.py decide`: model-death → bounded
retry marker (reconcile re-dispatches, no attempt burned — DRE-2018);
cap → hold/park; genuine no-progress → escalate. Death cap counts CONSECUTIVE
worker-bot deaths since the last successful push.

**Verdict:** GAP — filed DRE-2120 (hardcoded qa-bot login literal at the
trigger job-if; shared with merge-gate.yml).

## medic.yml — failure triage (reusable)

Stub binds `workflow_run: completed` over the watched workflow roster.

**Q1 — actor.** Runs on conclusion/attempt gates, not actor gates; the
diagnosis agent's `allowed_bots: "*"` is medic's exclusive, test-pinned
privilege (`NoNewWildcardTest`) — justified because the medic must run for ANY
failing actor and only writes diagnoses (Linear comments/cards), never code or
merges.

**Q2 — secrets.** workflow_run events carry full repo secrets even when the
failed run was dependabot-actor'd (the documented GitHub behavior); the only
failing dependabot-triggered workflow here is Pipeline Tests, whose rerun
needs no secrets anyway (hardcoded test env).

**Q3 — vendor behavior.** Exactly one automatic rerun, keyed off GitHub's own
`run_attempt` (retry only at attempt 1, diagnose only at attempt ≥ 2) — the
vendor's counter, not a marker we could double-post.

**Q4 — command limitations.** The diagnosis agent uses `github.token`, NOT the
minted App token, because the App deliberately lacks `actions:read` and would
diagnose blind (DRE-1346); the stub grants the access.

**Q5 — crash mid-flow.** Classify-FIRST (DRE-1921/`medic_classify.py`): an
infra crash (rate-limit, auth-startup death) gets NO rerun and NO diagnosis —
a single 🔌 "reviewer down, not a code rejection" note — because re-running
against an exhausted limit deepens it. Log-fetch failure degrades to normal
handling, never a blind loop.

**Verdict:** covered.

## linear-sync.yml — merge → card Done (reusable)

Stub self-linear-sync.yml binds `pull_request: [closed]` only.

**Q1 — actor.** The closed event fires as whoever closed/merged: qa-bot merges
(normal secrets), humans, or dependabot closing its own superseded PRs — the
stub's job-if excludes the dependabot actor (see Q2). The card is extracted
ONLY from the head ref, anchored `agent/DRE-<n>-` with a required delimiter —
title/body text can never Done someone else's card
(`test_linear_sync_done_gate.py`).

**Q2 — secrets.** `github.actor != 'dependabot[bot]'` at the stub job-if
(self-linear-sync.yml:23): a dependabot-closed PR would get the empty
Dependabot store and die at the mint; those events need no sync anyway
(DRE-2047 class).

**Q3 — vendor behavior.** Close-without-merge is a no-op (`merged == true`
guards both jobs). Done→Done is idempotent and the terminal guard in
`linear_ops.py state` never drags a completed/canceled card back — a replayed
event cannot reopen a card.

**Q4 — command limitations.** The conflict-sweep dispatch uses `github.token`
(the App token 403s on the Actions API); the double `--conflicts-only` sweep
(45 s / 60 s) exists because GitHub recomputes mergeability lazily.

**Q5 — crash mid-flow.** Steps are sequential accelerations of the `*/15`
reconcile cron: a crash after `state Done` leaves promotion/epic-close to the
next sweep (≤15 min), by design. The only replay artifact is a possible
duplicate "✅ Merged" comment — cosmetic.

**Verdict:** covered.

## reconcile.yml + scripts/reconcile.py — the sweep

Stub self-reconcile.yml binds `schedule: */15`, `repository_dispatch:
[reconcile]`, `workflow_dispatch`.

**Q1 — actor.** Two-token design, each chosen for the gate it must clear:
`gh workflow run` dispatches ride `GH_DISPATCH_TOKEN` (= `github.token`,
`actions:write`; the App token 403s — DRE-1254), so dispatched runs initiate
as `github-actions` — admitted by every allowlist
(`EverySiteAdmitsGithubActionsTest`, DRE-2053). `repository_dispatch`
redispatches ride the App token (`contents:write`), initiating as
`agent-bureau-bot` — admitted. In this repo the sweep dispatches the STUBS
(pr-review.yml, self-agent-fix.yml, self-merge-gate.yml) because dispatching a
workflow_call-only file 422s.

**Q2 — secrets.** Cron/dispatch contexts always carry full repo secrets; no
dependabot-actor path reaches this workflow.

**Q3 — vendor behavior.** A dependabot rebase changes the head sha, and the
sha-bound dependabot dispatch receipts re-arm a fresh budget on the new head —
the DRE-2071 fix. Verdict suppression (`has_verdict`) is likewise sha-bound to
the current head.

**Q4 — command limitations.** Dispatch pacing: `DEPENDABOT_DISPATCH_CAP=3` per
sweep, deferred tail reported rather than dropped.

**Q5 — crash mid-flow.** No cross-sweep in-memory state: every counter is
re-derived from Linear comment counts and GitHub run/PR state, every write is
guarded by a once-per-card/sha marker, and `cancel-in-progress: false` +
`timeout-minutes: 10` mean a crashed sweep just yields to the next cron tick.
Receipts are outcome-aware: `_review_dispatch_in_flight()` resolves the
dispatched run's real outcome, a crashed review earns ONE bounded retry per
sha (`DEPENDABOT_RECEIPT_CAP=2`), and at cap the sweep fails LOUD instead of
silently freezing (both DRE-2071 lessons). Cancelled agent runs defer without
burning the requeue cap (DRE-2074); liveness is checked against GitHub's run
status via the 🧠 heartbeat URL before any death verdict (DRE-2032). Requeue
caps (`REQUEUE_CAP=2`) park to Backlog + `needs-human` at the bound.

**Verdict:** covered.

## red-main-repair.yml — cardless fixes to a broken main (reusable)

Stub self-red-main-repair.yml binds `workflow_run: [Pipeline Tests] completed`.

**Q1 — actor.** Job-if: conclusion failure AND head_branch == default branch.
The failing push may be qa-bot's merge, a pool bot, `github-actions`, or a
human — the repair agent's `allowed_bots` roster admits all bot cases, no
wildcard.

**Q2 — secrets.** workflow_run context carries full secrets (see medic). Pool
mint keyed `repair:<sha>` isolates quota from card work.

**Q3 — vendor behavior.** Attempt record = existing `repair/*` branches + PRs
of any state, read from GitHub itself; an unreadable record fails CLOSED (no
dispatch). Duplicate failure events queue on the per-repo concurrency lock and
no-op against the decide record.

**Q4 — command limitations.** Escalation/budget cards dedupe via
`linear_ops.py find-open` by exact title — terminal cards deliberately don't
count, so a re-broken main mints a fresh card.

**Q5 — crash mid-flow.** Bounded: 2 attempts per failing sha, then a triage
card. The repair never watches itself — its own failures route to the medic.
No PR and no escalation file → loud red.

**Verdict:** covered.

## tests.yml — Pipeline Tests

Direct triggers: `pull_request` (all PRs) + `push: [main]`.

**Q1 — actor.** Any PR actor including dependabot[bot]; no actor gates — the
suite must run for everyone.

**Q2 — secrets.** Deliberately needs NONE: `LINEAR_API_KEY: test-key`,
`GH_TOKEN: test` are hardcoded stub env, so a dependabot-actor'd run with the
empty store still runs green — and installs from `requirements-dev.txt`
(Dependabot's pip manifest, DRE-2039) so a version-bump PR actually exercises
its new pins.

**Q3 — vendor behavior.** A dependabot rebase simply re-fires the suite on the
new head; no markers to invalidate.

**Q4 — command limitations.** The TDD gate exempts dependabot by
GitHub-attested `PR_AUTHOR` (DRE-2049), never the spoofable branch name;
branch/sha inputs ride env, not shell interpolation.

**Q5 — crash mid-flow.** Stateless; a failed run is the medic's workflow_run
signal and a rerun is safe.

**Verdict:** covered.

## The self-* stub family — this repo on its own rail

Eight stubs consume the reusables `@main` with `secrets: inherit` — the
deliberate canary channel (DRE-1929: the fleet rides human-promoted tags;
bureau-pipeline soaks every merge). Per stub:

- **self-agent-task.yml** — `repository_dispatch: [agent-execute]`; per-card
  concurrency, `cancel-in-progress: false`. App-actor only; no dependabot path.
- **self-plan.yml** — `repository_dispatch: [agent-plan]`; same shape.
- **self-agent-fix.yml** — `issue_comment: [created]` + `workflow_dispatch`;
  no stub actor guard — the reusable's qa-bot-author job-if is the gate, and it
  skips a dependabot-authored comment clean before any secret is used.
- **self-merge-gate.yml** — `workflow_run: [Pipeline Tests, QA Review]` +
  `issue_comment` + `workflow_dispatch`; `actions: write` for the fix
  dispatch; reusable job-ifs gate actors.
- **self-linear-sync.yml** — `pull_request: [closed]` with the ONLY stub-level
  dependabot actor guard besides pr-review.yml (dependabot closes its own
  superseded PRs with the empty store — DRE-2047 class).
- **self-medic.yml** — `workflow_run` over the nine-workflow roster;
  `actions: write` for the rerun.
- **self-reconcile.yml** — `schedule: */15` + `repository_dispatch:
  [reconcile]` + `workflow_dispatch`; `actions: write` feeds
  `GH_DISPATCH_TOKEN`.
- **self-red-main-repair.yml** — `workflow_run: [Pipeline Tests]`; guard lives
  in the reusable.

(pr-review.yml, the ninth stub, is audited in its own section above.)

**Q1 — actor.** Each stub's reachable actors are gated either at the stub
(linear-sync, pr-review) or at the reusable's job-if (fix, merge-gate,
qa-review) — verified per section above; `test_self_stub_dispatch_parity.py`
and `test_self_host_stubs.py` pin the stub set.

**Q2 — secrets.** `secrets: inherit` passes required-secret validation even
when the store is empty, so every stub whose events a dependabot actor can
fire needs a skip BEFORE the first mint — present on both such stubs
(self-linear-sync.yml, pr-review.yml) and on the reusables' job-ifs for the
comment-triggered ones.

**Q3 — vendor behavior.** `@main` consumption means a boundary fix here is
live on the next trigger — the fleet-repeat (v3) lesson lands on the RELEASE
channel, not on these stubs.

**Q4 — command limitations.** In-repo `gh workflow run` must target these
stubs, not the workflow_call-only reusables (422 — DRE-2056); reconcile and
merge-gate both carry that mapping.

**Q5 — crash mid-flow.** Stub crashes are the reusables' crashes — covered per
section; self-medic watches all of them, and the medic's own failures are the
one unwatched surface (accepted: it never mutates state beyond comments).

**Verdict:** covered.

## Pool dispatch — scripts/dispatch_pool.py

**Q1 — actor.** The selected slot's App token authors the PR, so the PR author
is `agent-bureau-bot` or `-2/-3/-4` — every allowlist that admits the worker
must admit the whole pool, enforced live by `test_worker_pool_allowed_bots.py`
(the DRE-2020 lockout, mechanically pinned). There is no generated single
source of truth for the rosters; the test IS the sync mechanism.

**Q2 — secrets.** The selector sees app ids and short-lived probe tokens only,
never private keys (those stay in the workflow's per-slot mint clauses).
Missing slots shrink the pool gracefully.

**Q3 — vendor behavior.** Quota-aware: picks the slot with max
`resources.core.remaining` via the quota-exempt `/rate_limit` probe — the
2026-06-28 shared-bucket exhaustion is the incident this design answers.

**Q4 — command limitations.** Actions cannot index secrets dynamically →
explicit per-N mint clauses; ties break by `sha256(card-id)` so re-runs pick
the same slot deterministically (never `hash()`/randomness).

**Q5 — crash mid-flow.** Any selector exception routes to slot 1 with
`reason=selector-error`, exit 0 — selection can never block a build and leaves
no partial state.

**Verdict:** covered.

## Duplicate-dispatch guard — scripts/dedupe_dispatch.py

**Q1 — actor.** Runs inside agent-task before any state mutation; reads only
worker-authored heartbeats and open `agent/<DRE-N>` PRs (`\b`-anchored so
DRE-205 never matches DRE-2053).

**Q2 — secrets.** Uses the run's own worker token + LINEAR_API_KEY; no
separate event context.

**Q3 — vendor behavior.** Liveness is GitHub's own run status, not a
timestamped marker: a heartbeat whose run is `completed` (crashed or finished)
does NOT block — the rebuild proceeds (DRE-2057). Closed/merged twin PRs are
invisible by design.

**Q4 — command limitations.** `gh pr list` (full list, not search) because the
search index lags seconds behind — exactly the window a twin dispatch lives in.

**Q5 — crash mid-flow.** FAIL-OPEN by design (it gates a build, not a merge):
any unreadable input proceeds, worst case a twin PR. The skip receipt carries
the `🤖` machine prefix so reconcile never reads it as a human blocker reply,
and every downstream step honors the skip so a skipped dup is never reported
as a dead run (which would requeue-loop).

**Verdict:** covered.

## @dependabot commands + .github/dependabot.yml

The vendor-command surface itself: what we ask Dependabot to do, and what its
config makes it do.

**Q1 — actor.** Dependabot's own opens/rebases/closes fire events as
`dependabot[bot]` — admitted where reviews must run (qa-review/verify
allowlists), skipped where the empty store would crash a mint (stub/reusable
job-ifs), exempted at the TDD gate by attested author. Command replies (e.g.
the DRE-2062 error reply) are dependabot-authored comments that skip every
comment-triggered gate clean.

**Q2 — secrets.** Dependabot-triggered `pull_request` contexts get the
SEPARATE, for-us-empty Dependabot secrets store plus a read-only token
(DRE-2047/2067) — the reason the real review rides reconcile's
`workflow_dispatch` instead.

**Q3 — vendor behavior.** Closing a Dependabot PR does not end the update —
it re-files on the weekly schedule. Ignoring one major invites the next
(DRE-2064: ~19 walk-down PRs, a critic review burned per rung). A rebase
changes the head sha every receipt and verdict was bound to (handled: both are
sha-keyed and re-arm). **Unhandled:** every major the gate parks as `human`
stays open indefinitely and counts against `open-pull-requests-limit: 5` —
at the bound Dependabot silently stops opening NEW version PRs, including the
security-relevant minor/patch group, with no alert anywhere. Filed.

**Q4 — command limitations.** `@dependabot ignore this major version`
suppresses only THAT major (walk-down, DRE-2064); `@dependabot ignore` is
single-dependency only and fails on grouped PRs (DRE-2062); `@dependabot
rebase` is operator-only today (DRE-2071's backlog recovery). Pipeline code
posts NO @dependabot commands — deliberately, after DRE-2064. **Unhandled:**
that leaves no safe, documented rejection path for a parked major; the durable
mechanism (a config `ignore` stanza — dependabot.yml currently has none) is
nowhere written down as the playbook. Filed.

**Q5 — crash mid-flow.** Config-side state is Dependabot's, not ours — no
receipts to strand. Our side's crash recovery for its PRs lives in the
reconcile receipts (outcome-aware, bounded, sha-keyed — covered above).

**Verdict:** GAP — filed DRE-2118 (safe major-rejection playbook + config
`ignore` path) and DRE-2119 (WARN/CRITICAL before parked majors starve the
minor/patch group at `open-pull-requests-limit: 5`).

---

## Closing note

Sixteen surfaces audited; twelve fully covered by the post-rollout fixes
(DRE-2020 through DRE-2074 all verified live in the code, not just in the
standard's incident list); four gaps found on paper and filed: DRE-2117,
DRE-2118, DRE-2119, DRE-2120. No behavior was changed by this audit.
