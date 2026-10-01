# Every build and fix run gets 400 turns — the proof, read 2026-09-29

The proof for [DRE-4371](https://linear.app/dreadnoughtfoundry/issue/DRE-4371),
the closing card of epic [DRE-4359](https://linear.app/dreadnoughtfoundry/issue/DRE-4359).

Read on **2026-09-29 between 18:40 and 18:52 PT** against live Linear and live
GitHub Actions on `dreadnought-foundry/bureau-pipeline` (plus one run in
`dreadnought-foundry/agent-bureau`). Every time below is **Pacific (PDT, UTC−7)**.
Where a UTC string is itself the evidence, it is quoted as written with the
Pacific reading beside it.

**Two of the four observations hold. The other two were never run, and the
CEO closed the proof without them on 2026-09-30 (see "Closing decision").**
Observation 1 (every run at 400) and observation 4 (the decide-by checkpoint)
were read from traffic the pipeline produced on its own. Observations 2 and 3
needed planted `turns:150` cards, and none was planted. They are recorded as
**NOT OBSERVED**, and nothing below describes an outcome that was not seen.

**Read-only so far.** Nothing here moved a card, dispatched a run, re-ran a
workflow or commented on a pull request. This record is the only write.

---

## The window

Every sibling is Done, and each one's merge is on `main`:

| card | what it shipped | merged (PT) | PR |
| -- | -- | -- | -- |
| DRE-4361 | every build and fix run gets 400; `turn_budget.py` learns the decide-by turn | 2026-09-24 15:28 | [#496](https://github.com/dreadnought-foundry/bureau-pipeline/pull/496) |
| DRE-4364 | `agent-fix.yml` selects its ceiling through `turn_budget.py` | 2026-09-24 17:35 | [#497](https://github.com/dreadnought-foundry/bureau-pipeline/pull/497) |
| DRE-4366 | a turn-ceiling death is read before it is retried | 2026-09-29 10:45 | [#554](https://github.com/dreadnought-foundry/bureau-pipeline/pull/554) |
| DRE-4368 | a requeued run resumes the dead run's branch | 2026-09-29 14:05 | [#558](https://github.com/dreadnought-foundry/bureau-pipeline/pull/558) |
| DRE-4370 | the build prompt names the ceiling and the decide-by turn | 2026-09-29 16:56 | [#568](https://github.com/dreadnought-foundry/bureau-pipeline/pull/568) |
| DRE-4362 | the decision record (agent-bureau, hand-built) | Done 2026-09-20 10:03 | — |

bureau-pipeline's own `self-agent-task.yml` and `self-agent-fix.yml` ride
`@main` (bureau-pipeline is the canary channel). So a bureau-pipeline run that
starts after a merge runs the merged code, with no channel lag. At the time of
reading, `stable` and `main` were the same commit, `1cc4e3b5` (#570).

---

## 1. Every run is at 400 — OBSERVED

### 1a. The first build run after DRE-4361

**How it was found.** I listed every `self-agent-task.yml` run in bureau-pipeline
created between #496's merge (2026-09-24 15:28 PT) and 2026-09-24 23:00 PT.

- **Run [36067633973](https://github.com/dreadnought-foundry/bureau-pipeline/actions/runs/36067633973)**
  (created 2026-09-24 15:28:34 PT, 23 seconds after the merge) does not count.
  It was DRE-4364's dispatch, and its dedupe gate stopped it before any model
  step ran. Its log reads:
  `reason=open agent PR #497 (agent/DRE-4364-fix-turn-budget-select) already exists for DRE-4364 — duplicate dispatch`.
  It posted no `🧠 model-attempt`.
- **Run [36077685934](https://github.com/dreadnought-foundry/bureau-pipeline/actions/runs/36077685934)**
  (card [DRE-4758](https://linear.app/dreadnoughtfoundry/issue/DRE-4758)) is the
  first build run that reached the model. Its receipt was posted to the card at
  **2026-09-24 17:28:42 PT** (`2026-09-25T00:28:42.513Z`). Quoted verbatim:

  > 🧠 model-attempt: claude-opus-5 — engineer agent starting (turns=400). model-policy: claude-opus-5 chosen for engineer (workhorse kind) — top of the ladder, nothing above it was skipped turn budget 400: the default — this card carries no 'turns:' or 'size:' label. Run: https://github.com/dreadnought-foundry/bureau-pipeline/actions/runs/36077685934

**Expected:** `turns=400`, and a label clause that is no longer blank (the
defect read "this card carries no  or  label"). **Seen:** `turns=400`, and the
clause names both labels: `no 'turns:' or 'size:' label`. The run shipped
[#499](https://github.com/dreadnought-foundry/bureau-pipeline/pull/499), merged
2026-09-24 18:20 PT.

The same shape holds after DRE-4368 added the resume clause. DRE-4699's receipt
at 2026-09-29 16:56:52 PT, run
[36647801502](https://github.com/dreadnought-foundry/bureau-pipeline/actions/runs/36647801502):

> 🧠 model-attempt: claude-opus-5-5 — devops agent starting (turns=400, resume=none (no card branch)). model-policy: claude-opus-5-5 chosen for devops (workhorse kind) — top of the ladder, nothing above it was skipped turn budget 400: the card's 'size:L' label. Run: https://github.com/dreadnought-foundry/bureau-pipeline/actions/runs/36647801502

### 1b. The first fix run after DRE-4364

**How it was found.** I listed every `self-agent-fix.yml` run created between
#497's merge (2026-09-24 17:35 PT) and 2026-09-25 05:00 PT. That was 15 runs.
The first 9 concluded `skipped`, and no fix agent ran in them. The first run
that ran a fix agent:

- **Run [36091796670](https://github.com/dreadnought-foundry/bureau-pipeline/actions/runs/36091796670)**,
  "Agent Fix" on [#507](https://github.com/dreadnought-foundry/bureau-pipeline/pull/507)
  (`repair/DRE-4841-ee4e4314486f`), created **2026-09-24 20:47:35 PT**. Its log:

  ```
  2026-09-25T03:48:05.1283487Z turn budget 400: the default — this card carries no 'turns:' or 'size:' label.
  2026-09-25T03:48:16.9987433Z   claude_args: --max-turns 400
  ```

  (03:48:05 UTC = 2026-09-24 20:48:05 PT.)

This was a conflict-mode fix run ("merge-conflict resolution ONLY"). The
turn ceiling is chosen the same way in every mode.

**Expected:** `--max-turns 400` shown in the step summary (`turns_why`).
**Seen:** the `turns_why` line, `turn budget 400: …`. The step echoes that line
to both its log and `$GITHUB_STEP_SUMMARY`. The action also received
`--max-turns 400` in `claude_args`. **Two caveats.** The `turns_why` line says
`turn budget 400`. It does not contain the literal string `--max-turns 400`,
which appears only in `claude_args`. And I read the run **log**, not the
rendered step-summary page. The log carries the same line the step writes to
the summary.

---

## 2. A death past implementation green is retried once, from its own branch, and ships — NOT OBSERVED

Not planted. The card leaves the planted change open ("small but sits in a large
file — the DRE-3088 shape: two edits inside existing steps of `plan.yml` plus a
test"). The operator's rule for this run is that the change must be small,
harmless and genuinely useful, or else it waits for a proposal to be approved.
No candidate passed both tests in the time given:

- **The stale header of `plan.yml`** (lines 1–5 say the planner is triggered
  "when a card labeled agent:planner enters Todo" and "the human approves by
  moving children Backlog → Todo"; both have been false since DRE-2725 and
  DRE-3030). This is safe and useful, but it is the wrong shape. It is at the
  top of the file, so an agent fixing it never has to read deep into the steps.
  It would very likely finish well inside 150 turns, which would force a second
  plant.
- **Receipt text inside `plan.yml`'s steps.** Every candidate found sits inside
  the footprint of an open card: DRE-4718 (the roll-up route), DRE-5179 and
  DRE-5180 (planner slots), DRE-5136 (activation at the cap), and DRE-4248 (the
  🔁 re-review notice). A plant there would collide with real work at merge
  time.

The mechanism this observation proves is already deployed (DRE-4366, DRE-4368).
What it needs is a decision on the planted change.

## 3. A death before implementation green goes to Planning with its progress — NOT OBSERVED

Not planted, for the same reason plus one more. A card that "genuinely does not
fit" (three deliverables across a workflow and two scripts) has to pass
Planning first. Planning runs the planning classifier and then the pre-approval
critic (see DRE-4758's thread: `🧩 planning-shape`, then `plan-critic: stage=one-off`,
then `🚦 planning-route`). Both are built to catch exactly this card and route
it as an epic or roll-up rather than a one-off. If that happens, it is a finding
in its own right: the too-big card is stopped before any build run. But the
build-time `✂️` path would still not have been seen. Since DRE-4370, a 150-turn
run must also decide by turn 30. A too-big card may therefore hand back at
`⏳ 1/5` (exit 8) rather than die, and that reaches Planning by a different
receipt.

---

## 4. The decide-by checkpoint is in the prompt — OBSERVED

`#568` merged at 2026-09-29 16:56:04 PT. The next bureau-pipeline build run,
[36647801502](https://github.com/dreadnought-foundry/bureau-pipeline/actions/runs/36647801502),
ran on head `68b4c56d` (the #568 merge commit). Its agent posted this to
[DRE-4699](https://linear.app/dreadnoughtfoundry/issue/DRE-4699) at
**2026-09-29 16:57:18 PT** (`2026-09-29T23:57:18.046Z`):

> ⏳ 1/5 plan — continuing within 400 turns

A second run, [36651160993](https://github.com/dreadnought-foundry/bureau-pipeline/actions/runs/36651160993)
on [DRE-5242](https://linear.app/dreadnoughtfoundry/issue/DRE-5242), posted the
same words at **2026-09-29 17:37:27 PT**:

> ⏳ 1/5 plan — continuing within 400 turns

**Expected:** `continuing within 400 turns` or `handing back`. **Seen:** the
first, twice. Before #568 the same marker read differently. DRE-4758's, on
2026-09-24 at 17:32 PT, was `⏳ 1/5 plan formed`. So the wording comes from the
merged prompt and not from habit. DRE-4699's run shipped
[#570](https://github.com/dreadnought-foundry/bureau-pipeline/pull/570), merged
2026-09-29 17:36 PT.

---

## Closing decision — 2026-09-30, about 17:05 PT

The CEO closed DRE-4371 on this record as it stands: **"Yes let's close this."**
He was accepting the operator's recommendation to close on observations 1 and 4,
with observations 2 and 3 recorded as not observed.

**Observations 2 and 3 were not observed and were deliberately not run.** The
reasons:

- **No run has died at the new ceiling.** No build or fix run has run out of
  turns since the 400-turn change went live on 2026-09-24. The last turn-cap
  deaths on the board were 2026-09-04 to 2026-09-08, all under the old 150-turn
  ceiling. So the requeue paths these observations exercise have had nothing
  real to act on.
- **Planting was not worth the cost.** Planting the two `turns:150` cards would
  have cost two agent runs at roughly $13–23 each, in `plan.yml`, a file three
  other open cards were editing at the time. A plant there would collide with
  real work at merge.

**Follow-up owed:** the first real build or fix run that dies at the 400-turn
ceiling past implementation-green (`⏳ 3/5`) is the observation for §2. The
same goes for one that dies at or before `⏳ 2/5` for §3. When one happens,
it is quoted into this record (card id, run URL, receipts verbatim, PT
timestamps) under that section.

---

## Something seen on the way, not part of this proof

At 2026-09-29 18:43 PT, DRE-5242's PR #571 got a `🛑 runner-environment-hold`
blaming "executable-not-found: the action could not find a Claude executable".
But the critic's own verdict on the same commit (`da036438`) names a real test
failure in `tests/test_lane_contract.py`, and Pipeline Tests run
[36653738122](https://github.com/dreadnought-foundry/bureau-pipeline/actions/runs/36653738122)
concluded `failure`. The hold appears to misread a red suite as a runner fault.
It is recorded here only so it is not lost. Its owner is the reviewer-environment-hold
work, not this epic.

## Acceptance criteria, as observed

- [x] The first build and fix runs after deploy are quoted, showing 400 turns and a non-blank label clause (§1a, §1b).
- [ ] A planted `turns:150` card died past `⏳ 3/5`, was requeued once from its own branch, and merged — **NOT OBSERVED, not planted; closed without it by the CEO's decision** (§2).
- [ ] A second planted card died at or before `⏳ 2/5` and landed in Planning — **NOT OBSERVED, not planted; closed without it by the CEO's decision** (§3).
- [x] A real run's `⏳ 1/5` marker is quoted with its decide-by wording (§4).
- [ ] This record merged on `main` — pending; this record is the pull request.
- [x] The CEO closes this card after reading the record — closed 2026-09-30 about 17:05 PT, on observations 1 and 4 with 2 and 3 not observed (see "Closing decision").
