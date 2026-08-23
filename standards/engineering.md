# Engineering standard — the shared discipline

The base rules for any agent that writes code. Role briefs add specifics; this
is the floor. Every rule here exists because its violation shipped a bug.

## Build discipline
- **TDD, split commits.** Commit the failing test FIRST, the implementation
  second. Git history must show the test existed before the fix. Same-commit
  does not count, and the order is **not repairable by adding a commit** —
  fixing it means rewriting the branch's history, which the fix loop cannot do
  (DRE-2694: three hours and two review rounds on work that was never wrong).
  Get it right on the second commit, where it is free.
- **Scope = exactly the card.** No drive-by refactors, no scope creep. If the
  card is wrong or ambiguous, STOP and write the blocker — a wrong guess costs a
  full review cycle.
- **Copy, not rebuild.** Almost everything you need already exists (the relay,
  the pipeline scripts, the proxy, the backend template, the alert engine, the
  CDK stacks). Reuse and adapt the proven asset; do not re-derive it.
- **Match the codebase.** Mirror existing naming, comment density, and test
  patterns. Read neighboring code before writing yours.
- **Empty-diff check.** Before opening the PR, `git diff --stat <default>...HEAD`
  must show real changes. Zero changed files = you did not do the work; stop and
  report a blocker.
- **Honesty about state.** Never claim tests pass without running them this
  session; never claim a PR is open without the `gh pr create` output in hand.
  Report failures as failures. Never claim "merged"/"deployed" without
  authoritative proof (`gh pr view --json mergedAt`).
- **A change that contradicts a document updates that document in the SAME PR.**
  Not a follow-up card, not a TODO. If your change makes a runbook, README or ADR
  that names the mechanism you touched wrong, fix it in this PR — or say in the PR
  body why it still holds. Deferring the doc is how the record rots: **~57 of
  agent-bureau's 102 ADRs describe a platform retired in June, none marked
  superseded, and every one of them was accurate when written.** Each went stale
  in a PR that shipped without touching it. A concrete cost: `docs/operator-runbook.md`
  went stale within a DAY of the code change that outdated it, and an operator
  following it would have provisioned four secrets, watched them work for eight
  hours, and had the chain die silently.

## Delegating work that will commit
**Every gate the work must pass goes in the brief, at the moment of handoff.**
A dispatched agent receives this standard through workflow context injection.
Every other writer — a coordinating agent, an operator session, a sub-agent
handed a task brief — receives nothing but the repo, the tools and the
credentials. **Habit does not travel.** If you are applying a rule because you
know it, the writer you hand the work to does not.

The evidence (DRE-2694, 2026-08-23): the `tdd` job has been unchanged since
2026-07-11 and hand-built work honoured it flawlessly for six weeks, in-session,
where the discipline was already in context. The day hand-built CODE work was
first handed to a sub-agent working from a brief that never mentioned the rule,
two PRs broke it within hours — one blocked three hours on a change whose
content was never wrong. Nothing regressed; the delegation did.

So when you hand off work that will commit, write down: the test-first commit
order, the local checks that must be green, and any required check the CI
publishes. **Authorship will not tell you afterwards** — a sub-agent's commits
carry the delegating human's git identity, so the record cannot distinguish
"the operator worked in-session" from "the operator dispatched a sub-agent".

## Test rigor — no vacuous tests
Every test must FAIL if the behavior it claims to verify is removed. Before the
PR, mentally revert your change and confirm the test goes red. A test that
passes against the unmodified codebase proves nothing and the critic rejects it.
- **Scenario + adversarial tests are mandatory when a feature touches ≥2
  systems** — unit-green ≠ live-working. Codify the hand-walk as a test.
- **Integration harnesses in Python, never bash.**
- **Repro a CI failure before claiming a fix** — local `gh`/tooling is authed
  and gives a false green; stub the live tool to fail and run the WHOLE suite.

## Don't fight over shared files
- **Each card/agent owns DISJOINT files.** A shared barrel, registry, route
  table, or gallery index edited by two open PRs conflicts every sibling. If
  registration would touch a shared file, make discovery convention-based
  (glob) so later work only ADDS files — or serialize with a formal blocker.
- **AST-sweep all call sites when you add a kwarg** — not one sentinel call site.
- **Producer-consumer drift:** extract a shared module OR update all callers in
  the same commit.
- **No case-colliding filenames** (`agentDetail.ts` beside `AgentDetail.tsx`):
  on macOS/Windows the WRONG file imports and the app renders blank while Linux
  CI stays green. Differ by more than case; a CI guard enforces this.

## Migrations
- **Renumber immediately before the PR.** `git fetch origin <default>`, set your
  revision to (highest merged head)+1 with `down_revision` at that head. Sibling
  cards merge migrations while you work — a stale number breaks the chain.
- **A migration is part of every deploy** (`upgrade head` on the just-built
  image, before new code rolls out, abort on failure). One canonical head — a
  double-head makes `upgrade head` ambiguous.
- **`NOT NULL` backfill crashes a live system.** Add nullable → backfill →
  separate release sets `NOT NULL`. Widen `alembic_version` to `varchar(255)`.

## Blockers & heartbeats
- **Blocked-by is a relation, not prose.** Every dependency is a real Linear
  `blockedBy` relation, never just English in the description (prose leaves the
  reconcile/auto-close gates blind). Never name the parent epic as a blocker —
  epics stay In Progress and would deadlock.
- **Heartbeats:** post one line per phase to the card
  (`⏳ <n>/5 <label>`): 1/5 plan · 2/5 RED · 3/5 green · 4/5 local checks ·
  5/5 PR opened. Never skip 1/5 (it is your "alive" signal). Reporting must
  never block the build.

## Self-hosting convention (supersedes the retired Operator-card convention, 2026-07-11)
bureau-pipeline IS a dispatch target (DRE-1929 Option A; ADR
`adr-bureau-pipeline-self-host.md` in agent-bureau — "agents author, human
promotes", CEO sign-off recorded on DRE-1925). Agent-authored PRs to this repo
are the DESIGNED path: cards dispatch, engineer agents build, the critic
reviews, the gate merges to `main`. The trust boundary is NOT authorship — it
is (1) the authenticated critic + gate (sha-bound verdicts, author ≠ merger
enforced by separate App identities), and (2) HUMAN RELEASE PROMOTION: the
fleet consumes tagged releases (`vN`, paired `pipeline_ref`), never this
repo's live `main`; only agent-bureau and bureau-pipeline ride `@main` as the
canary channel. A bad merge here stages a bad NEXT release — it cannot
silently reprogram the fleet; cutting/re-pointing the release tag is a human
act and the permanent gate. DRE-2551 added `stable`, a moving tag this repo
advances by itself on every harness-proven commit on `main` — no product repo
pins it, and the `vN` cut it stages is still the human act described here
(`docs/self-hosting.md`).

Reviewers: verify the verdict binds the head sha and the gate identities held.
Do NOT block on agent authorship — that convention is retired.

## Acceptance
Your PR merges only when every check is green AND the critic verdict is APPROVE
(and, for UI/multi-system cards, the verifier PASSes). Optimize for first-pass
green: run everything you can locally before pushing.

See also: `standards/card-quality.md`, `standards/architecture.md`,
`standards/comms.md` (for any message you post to the CEO).
