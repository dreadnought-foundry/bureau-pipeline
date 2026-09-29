# The 06:00 PT groomer posted its proposal at 06:21 PT on 2026-09-29 with a verify verdict on all 26 cards, for $4.81 and 1 min 29 s — the proof record

The proof for [DRE-4973](https://linear.app/dreadnoughtfoundry/issue/DRE-4973),
from epic [DRE-4967](https://linear.app/dreadnoughtfoundry/issue/DRE-4967).
Every time below is Pacific (PDT, UTC−7). Observed by hand, read-only, against
the live Actions runs in this repository, their uploaded verdict artifacts, the
live Linear board and the code on `main`, on 2026-09-29 between 11:05 and
11:25 PT.

**What happened.** The `0 13 * * *` cron (06:00 PT) fired on its own and GitHub
started the run at 06:07:51 PT. The gate said the groomer runs. The groom step
took 11 min 15 s. Then 26 `verify` legs ran in parallel against `main` and
all 26 came back: 25 `still-needed`, 1 `unverified`, none `done` or `obsolete`.
The post job put the proposal on the standing card DRE-4541 at
**06:21:15 PT**, 8 min 45 s before the 06:30 PT line. The page carries the
line `Verify step: 26 cards, $4.81, 1 min 29 s wall clock`.

**Three things do not match the card as written.**

- **This is not the first scheduled morning after the wiring card merged.** The
  wiring card, DRE-4972, merged on 2026-09-27 at 11:01 PT (bureau-pipeline
  PR #536). The first scheduled morning after it was 2026-09-28 (run
  [36426267250](https://github.com/dreadnought-foundry/bureau-pipeline/actions/runs/36426267250)).
  It ran 29 legs and every one came back `unverified`; its page said
  `Verify step: 29 cards, cost unknown, 0 min 36 s wall clock`. The scheduled
  run's actor, `agent-bureau-qa-bot`, was not in `allowed_bots`. DRE-5123 fixed
  that (PR #550, merged 2026-09-28 19:06 PT). This record observes the first
  scheduled morning after **that** fix, because the 09-28 morning had no
  verdicts to observe (§6).
- **The cost and the wall clock are both below the epic's prediction.** $4.81
  is $3.19 under the $8–30 range. 1 min 29 s is 2 min 31 s under the
  4–6 minute range (§3).
- **The gate line does not literally say `06:00 PT`.** It says `it is 06:07 PT`.
  The gate prints the clock when it ran, and by design it accepts the whole
  06:00–06:59 PT hour. The cron line that fired is the 06:00 PT one (§1).

---

## 1. The schedule fired itself

```
$ gh run list -R dreadnought-foundry/bureau-pipeline --workflow Groomer --limit 12 \
    --json databaseId,event,createdAt,conclusion
36580277951 schedule 2026-09-29T14:07:26Z success
36572881258 schedule 2026-09-29T13:07:51Z success
36433715563 schedule 2026-09-28T14:08:25Z success
36433520016 repository_dispatch 2026-09-28T14:06:53Z success
36426267250 schedule 2026-09-28T13:06:38Z success
...
```

`self-groomer.yml` on `main` (a611db66) carries the two crons
`0 13 * * *` and `0 14 * * *`, and passes `--card "${{ vars.GROOM_PROPOSAL_CARD }}"`
to the gate. The gate's log shows the variable rendered as `DRE-4541`.

| | The run that did the work | The other cron |
|---|---|---|
| Run | [36572881258](https://github.com/dreadnought-foundry/bureau-pipeline/actions/runs/36572881258) | [36580277951](https://github.com/dreadnought-foundry/bureau-pipeline/actions/runs/36580277951) |
| Event | `schedule` | `schedule` |
| Cron line | `0 13 * * *` (06:00 PT in summer) | `0 14 * * *` (07:00 PT in summer) |
| Created | 06:07:51 PT (7 min 51 s after the cron time) | 07:07:26 PT |
| Gate `why` | `it is 06:07 PT and DRE-4541 is open — the groomer runs` | `it is 07:07 PT, outside the 06:00-06:59 PT grooming hour — this is the other cron of the pair and it does nothing` |
| `schedule` jobs | groom, 26 verify legs and post, all success | skipped |
| Head sha | `26c888d5f4cb1fe9f1c35a8f2a4dd4470d60a672` | same |

The gate step, from `gh run view -R dreadnought-foundry/bureau-pipeline --job 109420967461 --log`:

```
gate  2026-09-29T13:07:57.5788971Z ##[group]Run python3 .bureau-pipeline/scripts/groom_schedule_gate.py --card "DRE-4541"
gate  2026-09-29T13:08:01.2674883Z it is 06:07 PT and DRE-4541 is open — the groomer runs
gate  2026-09-29T13:08:01.2676478Z linear-budget: 2441 → 2441 (spent 0 this run; window resets 07:07 PT; budget: undeclared)
```

and from the other cron (`--job 109446309869`):

```
gate  2026-09-29T14:07:33.7197602Z it is 07:07 PT, outside the 06:00-06:59 PT grooming hour — this is the other cron of the pair and it does nothing
```

The job timeline of run 36572881258, in PT:

| Job | Started | Finished | Took |
|---|---|---|---|
| gate | 06:07:54 | 06:08:03 | 9 s |
| schedule / groom | 06:08:05 | 06:19:20 | 11 min 15 s |
| schedule / verify × 26 | 06:19:22 (first) | 06:21:03 (last) | 1 min 41 s as jobs |
| schedule / post | 06:21:06 | 06:21:19 | 13 s |

## 2. The proposal landed on the standing card before 06:30 PT

The post job's log (`--job 109426308730`):

```
schedule / post  2026-09-29T13:21:15.4353432Z commented on DRE-4541
schedule / post  2026-09-29T13:21:15.4354646Z # Groom proposal `4a90676dea5f` — cycle 14
```

Linear's own record of it, read with `list_comments` on DRE-4541:

| Comment id | `createdAt` (UTC) | PT | Opens with |
|---|---|---|---|
| `d8929e85-9132-43ff-a12f-fd0295cd282c` | 2026-09-29T13:21:15.326Z | **06:21:15 PT** | `🧺 groom-proposal: 4a90676dea5f` |

That is 8 min 45 s before 06:30 PT. Of those 21 min 15 s after 06:00, GitHub's
late start took 7 min 51 s, the groom step 11 min 15 s, and the verify matrix
about 1 min 41 s.

The proposal proposes 20 of 186 Intake cards: 16 for Planning and 4 for Cancel.
Its verify section, quoted from the comment:

```
## Verified against main

A read-only agent read each card on the Planning list, and the spares behind it, against the code on main, and answered still-needed, done or obsolete with the file and line that proves it. A card it proved done or obsolete is on the Cancel list with that proof as its reason; a card it could not answer for stays where the proposal put it.

- still-needed: 25
- done: 0
- obsolete: 0
- unverified: 1

Verify step: 26 cards, $4.81, 1 min 29 s wall clock

Not verified — each stays where the proposal put it:

- DRE-4669 — no proof
```

## 3. Every verify leg and its verdict

The run has 26 `schedule / verify (<card>, <repo>)` jobs, all concluded
`success`. Each one uploaded a `groom-verdict-<card>` artifact. The table is
read from those artifacts (`gh run download 36572881258 -p 'groom-verdict-*'`),
and each verdict matches the leg's own log line `groom-verify: <card> — <verdict>`.

| # | Card | Repo read | Verdict | Agent cost | Agent time | First proof |
| -- | -- | -- | -- | -- | -- | -- |
| 1 | DRE-2563 | agent-bureau | **still-needed** | $0.20 | 31.5 s | `architecture/forensics/README.md:54` |
| 2 | DRE-2564 | agent-bureau | **still-needed** | $0.26 | 53.5 s | `architecture/wave-plans/wave-2-the-corpus.md:236` |
| 3 | DRE-2702 | bureau-pipeline | **still-needed** | $0.21 | 35.3 s | `scripts/merge_gate.py:1096` |
| 4 | DRE-3185 | agent-bureau | **still-needed** | $0.14 | 13.4 s | `console/backend/receipts.py:282` |
| 5 | DRE-3488 | bureau-pipeline | **still-needed** | $0.13 | 30.7 s | `.github/workflows/agent-task.yml:1440` |
| 6 | DRE-3489 | bureau-pipeline | **still-needed** | $0.15 | 36.4 s | `tests/test_platform_fault_scenario.py:85` |
| 7 | DRE-3530 | bureau-pipeline | **still-needed** | $0.27 | 31.9 s | `scripts/linear_ops.py:525` |
| 8 | DRE-3565 | agent-bureau | **still-needed** | $0.18 | 23.7 s | `scripts/check_runner_load.py:107` |
| 9 | DRE-3566 | agent-bureau | **still-needed** | $0.12 | 17.4 s | `scripts/check_runner_load.py:494` |
| 10 | DRE-3576 | bureau-pipeline | **still-needed** | $0.10 | 24.3 s | `.github/workflows/agent-fix.yml:2019` |
| 11 | DRE-3585 | bureau-pipeline | **still-needed** | $0.23 | 29.7 s | `scripts/linear_ops.py:525` |
| 12 | DRE-3681 | agent-bureau | **still-needed** | $0.20 | 29.4 s | `console/backend/fleet_credential_sync.py:69` |
| 13 | DRE-4140 | agent-bureau | **still-needed** | $0.21 | 16.8 s | `console/web/src/components/oneriver/workState.ts:721` |
| 14 | DRE-4324 | agent-bureau | **still-needed** | $0.14 | 22.9 s | `scaffold/customer-repo/.github/workflows/reconcile.yml:31` |
| 15 | DRE-4389 | bureau-pipeline | **still-needed** | $0.21 | 20.8 s | `.github/workflows/verify.yml:212` |
| 16 | DRE-4416 | agent-bureau | **still-needed** | $0.18 | 27.4 s | `console/backend/claude_usage_readings.py:61` |
| 17 | DRE-4572 | agent-bureau | **still-needed** | $0.14 | 20.7 s | `console/backend/schema.py:6923` |
| 18 | DRE-4574 | agent-bureau | **still-needed** | $0.12 | 21.4 s | `console/web/src/components/oneriver/RiverRow.tsx:300` |
| 19 | DRE-4666 | agent-bureau | **still-needed** | $0.25 | 37.5 s | `console/backend/poller.py:246` |
| 20 | DRE-4669 | agent-bureau | **unverified** | $0.25 | 37.5 s | none — reason: `no proof` |
| 21 | DRE-4676 | agent-bureau | **still-needed** | $0.27 | 36.5 s | `console/web/src/components/oneriver/Ribbon.tsx:341` |
| 22 | DRE-4723 | agent-bureau | **still-needed** | $0.25 | 31.1 s | `console/backend/intake_pen.py:391` |
| 23 | DRE-4914 | agent-bureau | **still-needed** | $0.15 | 18.7 s | `.github/workflows/standards-sync.yml:81` |
| 24 | DRE-4924 | agent-bureau | **still-needed** | $0.13 | 19.2 s | `architecture/decisions/adr-owned-runner-fleet.md:68` |
| 25 | DRE-5035 | agent-bureau | **still-needed** | $0.17 | 31.6 s | `console/backend/ddl/0065_morning_briefing.sql:1` |
| 26 | DRE-5036 | agent-bureau | **still-needed** | $0.14 | 24.4 s | `console/backend/morning_briefing_email.py:67` |

Sum of the 26 legs' `cost_usd`: $4.8097.

**The two numbers against the epic's prediction:**

| | Page says | Re-derived from the artifacts | Epic predicted | Inside? |
|---|---|---|---|---|
| Cost | $4.81 | $4.8097, the sum of 26 `cost_usd` | $8–30 | **No — $3.19 under the low end** |
| Wall clock | 1 min 29 s | 06:19:27 → 06:20:56 PT, the first agent `started_at` to the last `finished_at` = 89 s | 4–6 min | **No — 2 min 31 s under the low end** |

The legs ran on `claude-sonnet-5-5`. The dearest leg cost $0.27 (DRE-3530) and
the longest agent turn took 53.5 s (DRE-2564). Measured as jobs rather than as
agents, the matrix took 1 min 41 s (06:19:22 → 06:21:03 PT). That is also under
4 minutes.

## 4. One proof opened by hand

No leg answered `done` or `obsolete`, so the card's rule says to check one
`still-needed` proof the same way. I checked two. Both are in
`dreadnought-foundry/agent-bureau` `scripts/check_runner_load.py`, read at
`main` = `07bb503905569e9b29e496a7c9a42e5ad777d05f` (11:12 PT). No commit touched
that file on 2026-09-29, so it is the same text the agents read at 06:19 PT.

**DRE-3566** ("check_runner_load.py --full has no upper window bound"). The agent
said: *"assess_repo still takes only a lower bound and calls gh.runs(repo, since)
with no until, and the CLI has no --until flag."*

| Agent's proof | Line on `main`, verbatim | Matches? |
|---|---|---|
| `scripts/check_runner_load.py:494` | `def assess_repo(repo: str, *, gh, since: str = "2026-09-02") -> RepoRow:` | yes |
| `scripts/check_runner_load.py:501` | `    runs = gh.runs(repo, since)` | yes |
| `scripts/check_runner_load.py:985` | `    until = datetime.now(timezone.utc).strftime("%Y-%m-%d")` | yes |
| `scripts/check_runner_load.py:987` | `    load_rows = [assess_repo(full, gh=gh, since=args.since) for full in roster]` | yes |

The file's `add_argument` calls are `--year`, `--month`, `--since`,
`--bill-only`, `--full`, `--servers` and `--cache`. There is no `--until`. The
client's `runs()` (line 625) accepts an `until`, but `assess_repo` never passes
one. **I agree: still-needed.**

**DRE-3565** ("check_runner_load.py's class table does not know bureau-pipeline's
workflows"). The proofs at lines 107 (`_BY_BASENAME = {`), 136
(`"dependabot-auto.yml": "housekeeping",`), 152
(`return _BY_BASENAME.get(workflow_path.rsplit("/", 1)[-1], "unknown")`) and 817
(`if row.unclassified:`) are verbatim. None of `tests.yml`, `pr-review.yml`,
`release-gate.yml`, a `self-*` stub, `specimen.yml` or `red-main-repair.yml`
appears in the table between lines 107 and 152. **I agree: still-needed.**

## 5. The one unverified card

| Card | Reason on the page | Reason in the artifact |
|---|---|---|
| DRE-4669 | `no proof` | `"reason": "no proof"`, `"proof": []` |

The agent's own summary explains it. DRE-4669 is a roll-up epic whose leg
checked out `agent-bureau`, the card's `repo:` label. The agent found the
console half there, but *"the groomer and schedule half (groomer.py,
self-groomer.yml) is not in this checkout"*, so it would not call the epic
done. That is the fail-closed behavior DRE-4970 asked for: no proof, so no
verdict, and the card stays where the proposal put it. It also shows a limit.
A leg reads one repo, so a card whose work spans two repos can only ever come
back `unverified`.

## 6. What did not go to plan

- **The 09-28 morning, the first after the wiring card, verified nothing.** Run
  [36426267250](https://github.com/dreadnought-foundry/bureau-pipeline/actions/runs/36426267250)
  ran 29 legs at 06:06 PT on 2026-09-28. Its post log says
  `unverified: 29` and `Verify step: 29 cards, cost unknown, 0 min 36 s wall clock`.
  DRE-5123 records the cause: the scheduled actor was not in `allowed_bots`. The
  merged fix (bureau-pipeline PR #550, 2026-09-28 19:06 PT) is what made this
  morning's verdicts possible.
- **The prediction was high by about 2× on cost and 3× on time.** Each leg is
  one short read-only agent turn (13–54 s), and the matrix runs them all at
  once. The epic's $8–30 and 4–6 min do not describe that shape. Either
  the prediction or the brief's budget is worth another look. This record does
  not decide which.
- **The groom step ran longer than the workflow says it does.**
  `self-groomer.yml`'s header says "The run takes about eight minutes". This
  morning the `schedule / groom` job alone took 11 min 15 s (06:08:05 →
  06:19:20 PT), and the whole run took 13 min 28 s. On 2026-09-26 the same
  step took 10 minutes. The job's hard limit is `timeout-minutes: 35`, so
  nothing failed. But this is the number that decides whether 06:30 PT holds
  on a slower GitHub morning.
- **GitHub started the 06:00 cron 7 min 51 s late.** On 2026-09-26 the 06:15
  cron started 6 min 36 s late. Moving the cron to 06:00 (DRE-4969) is why
  this morning finished early instead of at 06:31 PT.

## What is not proven, and why

- **Other mornings.** One morning cannot show how often the post lands before
  06:30 PT. The groom step alone took 11 min 15 s. Add a GitHub delay above
  about 17 minutes and the post misses.
- **A `done` or `obsolete` verdict and the cancel path it feeds.** This morning
  had none. The Cancel list's four cards came from the ranking and the
  pre-post check, not from a verify leg. So the path where a verify proof
  becomes a cancel reason is not exercised here.
- **The other 23 `still-needed` proofs.** Two were opened by hand (§4); the rest
  are recorded as the agents gave them.
- **Which cron fired.** Inferred from the start time and the gate's printed
  hour, not read from a field.

## The card's criteria

- [ ] **The first scheduled morning run after the wiring card merged is
      observed live; the record names the run, quotes the gate's `06:00 PT`
      line, and records the PT time the proposal landed.** Run named
      (36572881258), gate line quoted (`it is 06:07 PT …`, the 06:00 PT cron),
      proposal at 06:21:15 PT: held. But it is the first scheduled morning after
      the *fix* DRE-5123, not after the wiring card DRE-4972. The morning after
      DRE-4972 (09-28) verified nothing. By the letter: partial.
- [ ] **Every `verify` leg listed with its verdict, the `Verify step:` line
      quoted, and each number stated against $8–30 and 4–6 min.** All 26 legs
      listed (§3). The line is quoted. Both numbers are stated, and both are
      outside the prediction, below it. Held as a record; the prediction missed.
- [ ] **One verdict's proof checked by hand against `main`, and every
      `unverified` card named with its reason.** Two `still-needed` proofs
      checked, lines quoted, and I agree with both (§4). DRE-4669, `no proof`
      (§5). Held.
- [ ] **The CEO closes this card after reading the merged record.** His call.
