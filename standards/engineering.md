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
- **Check the order yourself, before you push.** One command answers it while
  the branch is still local and a rebase is still yours to do:
  `python3 scripts/check_tdd_commits.py origin/<default> HEAD` (in a product
  repo the pipeline checkout puts it at
  `.bureau-pipeline/scripts/check_tdd_commits.py`). Exit 1 means reorder now.
  The same check runs in CI as `TDD commit discipline`, where the only remedy
  left is a human rewriting your branch — this is the last moment it is free.
- **The rule as it is ENFORCED, in one sentence.** That script is the rule and
  this is its restatement: *at least one commit touching `tests/` must appear
  **STRICTLY BEFORE** the first commit that changes non-test code.* One RED
  commit before the FIRST implementation commit — **not one before every
  implementation commit**. Docs-only and ops-only branches are exempt, and so
  is a `.py` change that is docstrings alone, and so is one confined to a
  GENERATED region — every differing line strictly between a `BEGIN generated`
  and an `END generated` marker, markers untouched (DRE-3896). That last one is
  safe only because the region's content is proved elsewhere: `python3
  scripts/sync_model_config.py --check` goes red in CI when a generated region
  does not match its canonical render, so code cannot hide in one. The proof is
  the condition, not a claim about it — the exemption applies **only to the
  handful of paths a generator's `--check` actually covers**, listed in
  `check_tdd_commits.py`'s `_GENERATED_REGION_PROOFS`; writing those markers
  into any other file buys nothing, because otherwise one reviewed commit
  adding two comment lines would give any file a permanent untested edit
  channel. A path with no proving generator, a line outside the region, an
  edited marker, a region with no END, or source that will not parse stays
  code. A static design record — a
  `.html`/`.md`/`.png`/`.jpg`/`.jpeg`/`.svg`/`.pen`/`.json` file under
  `console/design/` or a root `design/` — counts as docs; `.css` and source
  there stay code (DRE-3763). A root `models.json` counts as DATA, beside
  `config/` and `agents.yaml`: it is the Anthropic catalog snapshot a
  scheduled job refreshes from the vendor's model list, and there is no RED
  test to write for a list somebody else publishes (DRE-3879, CEO's signed
  answer 2026-09-16). **That one path, matched exactly** — a `models.json`
  anywhere else in the tree is somebody's source file and stays code, and the
  exemption says which FILES need a test committed first, never whether the
  rule applies: a `.py` change riding on the same pull request still owes its
  RED test first, and no check is skipped. **Reviewers: hold a branch to
  that rule and no other.** "A RED test immediately before each implementation
  commit" is a stricter standard nothing enforces, and blocking on it rejects a
  compliant branch — it cost agent-bureau #2247 ~22 hours, a fix-loop attempt
  and an operator decision (DRE-3005). Whether to tighten what is enforced is
  an operator/standards call; until it is made, the enforced rule is the rule.
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

## CI: narrow per change, whole every night
The CEO's decision, 2026-09-24. Over 2026-09-17 to 09-24, counting only jobs
that got a runner, the fleet spent about 16,800 CI minutes — roughly 40% of all
its runner time. Portico spent 8,850 of those running every suite on every
change, although 83% of its pull requests touched only one side or only docs.
The ask was at least 10% off with no coverage traded away. These five rules are
the shape that buys it, and they apply to every repo in the fleet.

1. **Per change, run only the suites the diff can reach.** A pull request, or a
   push to `main`, runs the suites its changed files can affect and skips the
   rest. The narrowing is a **script the repo's own tests execute against
   throwaway repositories** — Portico's `visual-paths-gate.sh`, agent-bureau's
   `ci_suite_filters.py` — and it **fails open**: any input it cannot read (a
   compare API that errors, a file list it cannot parse) runs everything.

   **A workflow `on: paths:` filter is NOT an acceptable narrowing**, and the
   reason is mechanical rather than stylistic: when the filter excludes a
   change GitHub **creates no check run at all** — the job is not skipped, it
   never exists — so a required check on that job never reports and the merge
   gate waits forever on a result nothing will post. A script that runs and
   decides to skip produces a check run concluding `skipped`, which rule 5
   admits. The filter produces nothing to admit.

2. **Every repo whose PR CI narrows carries a nightly `schedule:` run on
   `main` that runs every suite.** Narrowing is only safe where something still
   runs the whole thing: agent-bureau (DRE-3656), Portico (DRE-4804), and
   bureau-pipeline in `.github/workflows/tests.yml` under its own card. Crons
   stay off the hour and apart from each other, and outside the release window
   (`FLEET_WINDOW` in `scripts/release_train.py`, `standards/release-train.md`)
   — a nightly competing with a train for runners delays both.

3. **A red nightly is repaired.** The repo's Red-Main Repair stub
   (`.github/workflows/red-main-repair.yml`) is the rail, and it **must not
   filter `schedule` runs out**: it keys on `workflow_run.conclusion ==
   'failure'` and the head branch being the default branch, never on the event
   that started the run. A nightly nobody repairs is a nightly nobody reads.

4. **A missing nightly alarms.** The fleet watcher (DRE-4805) raises when a
   repo's nightly full run on `main` did not happen. It finds the nightlies by
   reading the workflow files, so **no list of repos is kept anywhere** — a
   repo that narrows its PR CI and forgets the nightly is caught by its own
   workflow file, not by somebody remembering to add it to a list.

5. **Skipped is green, and every narrowing depends on it.** The merge gate and
   the release train already count a `skipped` job as green —
   `GREEN_CONCLUSIONS` in `scripts/merge_gate.py`, which
   `scripts/release_train.py` imports rather than restates. Rule 1's narrowing
   relies on exactly that: the job runs, decides the diff cannot reach its
   suite, and concludes `skipped`. Drop `skipped` from that set and every
   narrowed pull request in the fleet stops merging — change it only with that
   consequence in hand.

## Don't fight over shared files
- **Each card/agent owns DISJOINT files, and that is checked at PLAN TIME.** It
  is not a request made of the builder — by the time a builder could honour it
  the cards are already cut, already parallel, and the collision is already
  paid for. The planner runs a contention pre-flight before it creates the
  cards: list the files each proposed card will create or edit, and where two
  lists intersect on a file that cannot be made append-only, carve a foundation
  card that OWNS that file first and block the others on it
  (`briefs/planner.md`). The plan critic reads the same question before the
  plan reaches the CEO. As a builder you inherit the answer: if you find
  yourself editing a file a sibling card also owns, that is a planning defect —
  say so rather than racing for it.
- A shared barrel, registry, route table, or gallery index edited by two open
  PRs conflicts every sibling. If registration would touch a shared file, make
  discovery convention-based (glob) so later work only ADDS files — or
  serialize with a formal blocker.
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
  epics stay In Progress and would deadlock. A `**Blocked by:**` line is
  documentation of the relation and cannot create one: since DRE-2676 a line
  claiming a dependency the board does not hold is a defect in the card, and
  the sweep routes it to `Triage` (`standards/card-quality.md`).
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
