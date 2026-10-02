# The medic's door, one full day on both repos — observed, 2026-10-01

The proof for [DRE-3648](https://linear.app/dreadnoughtfoundry/issue/DRE-3648).
The file name follows the card (`docs/medic-wake-proof-2026-09.md`); the day
observed is in October. Epic
[DRE-3621](https://linear.app/dreadnoughtfoundry/issue/DRE-3621) moved the
medic's wake set into the stubs, so a completion that did not fail is declined
at the door instead of leaving a run record of nine skipped jobs. This record
reads one full Pacific day of that door off the live Actions run lists of
`dreadnought-foundry/bureau-pipeline` and `dreadnought-foundry/agent-bureau`.
Every time below is Pacific (PDT, UTC−7) and comes from the run's own
timestamp.

**Read-only.** Nothing here re-ran, canceled or dispatched a run, and nothing
was written to Linear. Every number comes from the GitHub Actions REST API:
the run lists, each run's attempts, each medic run's job list, and the log of
each woken medic run's `classify` job.

**The claim held on both repos for the whole day.** No medic run ran a job for
a completion that did not fail: 788 declined runs in bureau-pipeline and 1,157
in agent-bureau, and every one of them carries exactly one job record, the
skipped `call`, and zero nested jobs (9 nested on 2026-09-29). And no failure
was missed: each of the 50 failed attempts of a watched workflow in
bureau-pipeline and each of the 106 in agent-bureau has exactly one medic run
that woke for it, 2 to 6 seconds after the failure. Three honest edges are set
out in §5: agent-bureau's stub rides `@stable`, not `@main`; no watched run
timed out that day, so only the `failure` half of the wake set was exercised;
and agent-bureau's Reconcile is not on its watch list.

---

## 1. What was live that day

The three build cards had all merged the day before, so 2026-10-01 is the
first full PT day after them.

| card | pull request | merged (PT) |
| -- | -- | -- |
| DRE-3625, the wake set declared once | [bureau-pipeline #591](https://github.com/dreadnought-foundry/bureau-pipeline/pull/591) | 2026-09-30 08:13:52 |
| DRE-3626, bureau-pipeline's stub declines at the door | [bureau-pipeline #596](https://github.com/dreadnought-foundry/bureau-pipeline/pull/596) | 2026-09-30 09:19:47 |
| DRE-3627, agent-bureau's stub declines at the door | [agent-bureau #2945](https://github.com/dreadnought-foundry/agent-bureau/pull/2945) | 2026-09-30 10:55:02 |

**Nothing about the door changed during the day.** The newest commit to each
file the door depends on predates it:

- bureau-pipeline `.github/workflows/self-medic.yml`: `c2ded767` (DRE-3626),
  committed 2026-09-30 08:49 PT.
- agent-bureau `.github/workflows/medic.yml`: `f52068a5` (DRE-3627),
  committed 2026-09-30 08:44 PT.
- bureau-pipeline `.github/workflows/medic.yml`, the reusable: `04db984c`
  (DRE-3625), committed 2026-09-30 07:53 PT. No commit has touched it since.

Both stubs carry the same door on their `call` job:

```yaml
if: (github.event.workflow_run.conclusion == 'failure' || github.event.workflow_run.conclusion == 'timed_out')
```

**Which reusable the woken runs ran.** Each woken run's `classify` log names
the commit it resolved. bureau-pipeline's stub calls `medic.yml@main`; its 50
woken runs resolved to 11 different `main` commits as `main` moved, the first
`db6adf6d` at 06:29:57 PT and the last `30d51fe3` at 22:13:44 PT.
agent-bureau's stub calls `medic.yml@stable`; its 106 woken runs resolved to 22
different `stable` commits as the channel advanced, the first `e35fa81b` at
00:22:19 PT and the last `4779215a` at 22:58:01 PT. All 22 distinct commits
across both repos descend from `04db984c` (GitHub's compare reads `ahead`,
`behind_by 0`, for each one), so every woken run ran the DRE-3625 reusable.

---

## 2. Before and after

The before column is the operator's reading of 2026-09-29, posted on the card
at 2026-09-30 17:45 PT, the day before the merges. The after column is
2026-10-01, read the same way (§6).

| | bureau-pipeline 09-29 | bureau-pipeline 10-01 | agent-bureau 09-29 | agent-bureau 10-01 |
| -- | --: | --: | --: | --: |
| Medic run records | 927 | **838** | 1042 | **1263** |
| Declined (conclusion `skipped`) | 892 | **788** | 992 | **1157** |
| Woke (ran a job) | 35 | **50** | 50 | **106** |
| Failed or timed-out attempts of a watched workflow | 35 | **50** | 50 | **106** ¹ |
| of which `timed_out` | — | **0** | — | **0** |
| Woke for a `success`, `skipped` or `cancelled` completion | 0 | **0** | 0 | **0** |
| Failures with no medic wake | 0 | **0** | 0 | **0** |
| Job records on a declined run | 9, all nested under `call` | **1, `call` itself** | 9, all nested under `call` | **1, `call` itself** |
| Nested jobs on a declined run | 9 | **0** | 9 | **0** |
| Declined runs whose job list was read | 10 sampled | **all 788** | 12 sampled | **all 1157** |

¹ 91 of agent-bureau's 106 failed attempts belong to runs created on
2026-10-01. The other 15 are 8 CI runs created between 21:17 and 23:40 PT the
night before that failed after midnight, including their re-run attempts. The
09-29 reading had the same shape: 30 created that day, 20 queued the night
before.

The card also gives an older baseline for bureau-pipeline alone, measured
2026-09-11 02:10 → 2026-09-12 02:10 UTC: 216 medic run records, 213 empty with
8 skipped nested jobs each, 3 that ran a job, 2 watched-workflow failures.

**The nested-job count was read on every declined run, not a sample.** The
card asks for one sampled declined run. All 788 declined runs in
bureau-pipeline and all 1,157 in agent-bureau were read. Each job list is
exactly `call: skipped`, with no runner and no nested job.

---

## 3. Sampled runs

Three runs per repo, as the card asks: one declined, one woken by a failure,
and the failed run that woke it.

**bureau-pipeline**

| when (PT) | run | what | job records |
| -- | -- | -- | --: |
| 2026-10-01 12:00:00 | [36911025511](https://github.com/dreadnought-foundry/bureau-pipeline/actions/runs/36911025511) | Pipeline Medic, declined: conclusion `skipped` | 1 (`call`, skipped), 0 nested |
| 2026-10-01 06:29:55 | [36869063157 attempt 1](https://github.com/dreadnought-foundry/bureau-pipeline/actions/runs/36869063157/attempts/1) | Groomer, `repository_dispatch` on `main`, conclusion `failure` (`drain / groom` failed) | 6 |
| 2026-10-01 06:29:57 | [36869103747](https://github.com/dreadnought-foundry/bureau-pipeline/actions/runs/36869103747) | Pipeline Medic, woke for 36869063157 attempt 1; `classify` and `retry` ran | 9 nested, 2 ran |

The retry it started was attempt 2, which also failed, at 06:31:39 PT. A second
medic run, [36869333360](https://github.com/dreadnought-foundry/bureau-pipeline/actions/runs/36869333360),
woke for that attempt at 06:31:42 PT and ran `classify` and `diagnose` instead
of a second retry. Each attempt was paired with its own wake.

**agent-bureau**

| when (PT) | run | what | job records |
| -- | -- | -- | --: |
| 2026-10-01 12:07:08 | [36911913220](https://github.com/dreadnought-foundry/agent-bureau/actions/runs/36911913220) | Pipeline Medic, declined: conclusion `skipped` | 1 (`call`, skipped), 0 nested |
| 2026-10-01 08:16:16 | [36882647096 attempt 1](https://github.com/dreadnought-foundry/agent-bureau/actions/runs/36882647096/attempts/1) | Agent Plan, `repository_dispatch` on `main`, conclusion `failure` (`call / bureau-card: DRE-5419` failed) | 2 |
| 2026-10-01 08:16:20 | [36882923192](https://github.com/dreadnought-foundry/agent-bureau/actions/runs/36882923192) | Pipeline Medic, woke for 36882647096 attempt 1; `classify` and `retry` ran | 9 nested, 2 ran |

The retry worked: attempt 2 of 36882647096 finished `success` at 08:17:29 PT.

---

## 4. The reading for decision (8)

| | bureau-pipeline | agent-bureau |
| -- | --: | --: |
| Medic run records, 2026-10-01 | 838 | 1263 |
| Medic runs that ran a job | 50 | 106 |
| Failed or timed-out watched attempts | 50 (0 timed out) | 106 (0 timed out) |
| Same three numbers, 2026-09-29 | 927 / 35 / 35 | 1042 / 50 / 50 |
| Same three numbers, 2026-09-11 baseline | 216 / 3 / 2 | not measured |

One wall accounts for most of the day's failures. Of the woken runs, 35 of 50
in bureau-pipeline and 61 of 106 in agent-bureau classified their failure as
`linear_rate_limited`; those runs ran `classify` and the `linear_rate_limited`
job. So the failure count the daily bar is set against was dominated by
Linear's rate limit on 2026-10-01, not by spread-out code failures. The rest of
the wakes: `retry` 8 and 24, `diagnose` 5 and 12, and a handful of
`upstream_outage`, `backoff`, `retry_declined` and classify-only runs (the
appendix lists every one).

What the jobs that ran look like across the day:

| jobs that ran in the woken run | bureau-pipeline | agent-bureau |
| -- | --: | --: |
| `classify`, `linear_rate_limited` | 35 | 61 |
| `classify`, `retry` | 8 | 24 |
| `classify`, `diagnose` | 5 | 12 |
| `classify` only | 1 | 6 |
| `classify`, `upstream_outage` | 1 | 0 |
| `classify`, `retry_declined` | 0 | 1 |
| `classify`, `backoff` | 0 | 1 |
| `classify`, `linear_rate_limited`, `retry_declined` | 0 | 1 |

---

## 5. Edges, said plainly

**agent-bureau's medic rides `@stable`, not `@main`.** The card's title calls
both repos "@main repos". The live agent-bureau stub reads
`uses: dreadnought-foundry/bureau-pipeline/.github/workflows/medic.yml@stable`
with `pipeline_ref: stable`, as every product stub has since DRE-2553. The
door being tested is the `if:` on the stub's own `call` job, and that lives in
agent-bureau's `main`, so the claim is still observed on agent-bureau. Only
bureau-pipeline's stub calls the reusable at `@main`.

**No watched run timed out that day.** All 156 wake-set completions were
`failure`; zero were `timed_out`, in either repo. The `timed_out` half of the
door was not exercised on 2026-10-01 and is not proven by this record.

**agent-bureau's Reconcile is not watched, by the repo's own rule.** Reconcile
failed 19 times in agent-bureau on 2026-10-01 (6 times on 2026-09-29), and no
medic run woke for any of them, because Reconcile is not on that stub's
`workflow_run` list. That is not a miss by this card's terms: the card counts
failures of watched workflows, and agent-bureau's watch-list rule
(`console/backend/tests/test_workflow_watch_lists.py`) requires every
`pull_request` workflow plus the pipeline stages, not the sweep.
bureau-pipeline's rule is wider (DRE-2036: every workflow that runs under its
own name), so its 11 red Reconcile runs were all woken. agent-bureau's one
failed `Release train` run is likewise unwatched on purpose: the medic never
retries a deploy. Nothing is filed here.

**One failure fell across midnight.** bureau-pipeline Pipeline Tests
[36974591550](https://github.com/dreadnought-foundry/bureau-pipeline/actions/runs/36974591550),
created 2026-10-01 23:39:17 PT, failed at 2026-10-02 00:00:19 PT. Its failure
belongs to 10-02 under the counting rule in §6, so it is not in the 50. It was
woken all the same, by
[36976308612](https://github.com/dreadnought-foundry/bureau-pipeline/actions/runs/36976308612)
at 00:00:22 PT. No woken run of 10-01 was for a failure outside the day.

**Records against completions.** Watched completions in the day number 839 in
bureau-pipeline (564 `success`, 221 `skipped`, 4 `cancelled`, 50 `failure`)
against 838 medic run records, and 1,271 in agent-bureau (761, 368, 36, 106)
against 1,263. The small gap is on the non-failure side only, and was not
chased: a completion that did not fail and left no medic record is still not
a wake, and every failure is paired.

---

## 6. Method

The same method as the 2026-09-29 reading, so the two columns compare:

- **Runs.** Every run of both repos created between 2026-09-29 00:00 PT and
  2026-10-02 02:00 PT, read from `GET /repos/{repo}/actions/runs?created=…`
  one hour at a time, so no window came near the API's 1,000-result cap. That
  was 5,568 runs in bureau-pipeline and 7,214 in agent-bureau. A second sweep
  of `status=failure` and `status=timed_out` runs created from 2026-09-01 up
  to that span found no older run that completed on 10-01.
- **Medic runs.** A `Pipeline Medic` run belongs to the day it was created.
  Declined means conclusion `skipped`; woke means it ran at least one job.
  Each medic run's job list was read from `…/runs/{id}/jobs?filter=all`.
- **Watched failures.** A failed attempt belongs to the day its completion
  falls in. Every attempt of every run of a watched workflow (each stub's
  `workflow_run` list as it stood on `main` that day) was read; a re-run's
  earlier attempts were read from `…/runs/{id}/attempts/{n}`.
- **Pairing.** Each woken medic run names its target in its own `classify`
  log: the `RUN_ID`, `RUN_ATTEMPT` and `WORKFLOW_NAME` the reusable is given.
  Every woken run was matched on (`RUN_ID`, `RUN_ATTEMPT`) to the attempt it
  served. No target was woken twice, and every target was a `failure`.
- **Timing check.** Every wake came 2 to 6 seconds after its failure's
  completion time (median 3 seconds in both repos), which confirms that the
  completion time used for the day boundary is the moment the medic was told.

---

## Did the claim hold

| acceptance criterion | 2026-10-01 | evidence |
| -- | -- | -- |
| The record is on `main`, covering one full PT day on both repos after all three build cards merged | **Not yet.** This record is in a pull request; it is on `main` only when that merges | §1: the last build card merged 2026-09-30 10:55 PT; the day read is 2026-10-01 00:00–24:00 PT |
| Zero medic runs ran a job on a `success`, `skipped` or `cancelled` completion; declined count per repo; nested jobs on a sampled declined run (expected 0, against 8 or 9) | **Held** | 0 and 0. Declined 788 and 1,157. Nested jobs 0 on every declined run, sampled 36911025511 and 36911913220 (§2, §3) |
| Each `failure` or `timed_out` watched completion is paired with a medic run that ran a job, run ids listed, none missing | **Held on `failure`; `timed_out` unexercised** | 50 of 50 and 106 of 106 paired, all listed in the appendix. No watched run timed out that day (§5) |
| The reading for decision (8) is in the document | **Held** | §4: 838 / 50 / 50 and 1263 / 106 / 106 |
| The CEO reads the record on `main` and closes the card himself | **Owed to the CEO** | Nothing here can satisfy it |

---

## What is owed

1. **This pull request merges**, which puts the record on `main`.
2. **The CEO reads it and closes DRE-3648.** No sitting is owed.
3. **`timed_out` stays unobserved** until a watched run times out. Nothing
   needs to be staged for it; the next day with a time-out will show it, and
   the expression is the same one the `failure` half proved.

Written 2026-10-02 from live traffic between 2026-10-01 00:00 PT and
2026-10-02 00:00 PT, read back after the day closed.

---

## Appendix: every failure and its wake

Each row is one failed attempt of a watched workflow whose completion fell on
2026-10-01 PT, and the one medic run that woke for it. The links open the
exact attempt. ¹ marks a run created the night before (§2).

### bureau-pipeline — 50 failed attempts, 50 wakes

| # | failed (PT) | workflow | run · attempt | medic woke (PT) | medic run | jobs that ran |
| --: | -- | -- | -- | -- | -- | -- |
| 1 | 06:29:55 | Groomer | [36869063157](https://github.com/dreadnought-foundry/bureau-pipeline/actions/runs/36869063157/attempts/1) · 1 | 06:29:57 | [36869103747](https://github.com/dreadnought-foundry/bureau-pipeline/actions/runs/36869103747) | classify, retry |
| 2 | 06:31:04 | Groomer | [36869130123](https://github.com/dreadnought-foundry/bureau-pipeline/actions/runs/36869130123/attempts/1) · 1 | 06:31:07 | [36869254286](https://github.com/dreadnought-foundry/bureau-pipeline/actions/runs/36869254286) | classify, retry |
| 3 | 06:31:39 | Groomer | [36869063157](https://github.com/dreadnought-foundry/bureau-pipeline/actions/runs/36869063157/attempts/2) · 2 | 06:31:42 | [36869333360](https://github.com/dreadnought-foundry/bureau-pipeline/actions/runs/36869333360) | classify, diagnose |
| 4 | 06:31:54 | Groomer | [36869130123](https://github.com/dreadnought-foundry/bureau-pipeline/actions/runs/36869130123/attempts/2) · 2 | 06:31:57 | [36869364109](https://github.com/dreadnought-foundry/bureau-pipeline/actions/runs/36869364109) | classify, diagnose |
| 5 | 07:07:44 | Groomer | [36873874048](https://github.com/dreadnought-foundry/bureau-pipeline/actions/runs/36873874048/attempts/1) · 1 | 07:07:47 | [36873915502](https://github.com/dreadnought-foundry/bureau-pipeline/actions/runs/36873915502) | classify, retry |
| 6 | 07:08:34 | Groomer | [36873874048](https://github.com/dreadnought-foundry/bureau-pipeline/actions/runs/36873874048/attempts/2) · 2 | 07:08:37 | [36874025655](https://github.com/dreadnought-foundry/bureau-pipeline/actions/runs/36874025655) | classify, diagnose |
| 7 | 07:33:33 | Agent Plan | [36877149670](https://github.com/dreadnought-foundry/bureau-pipeline/actions/runs/36877149670/attempts/1) · 1 | 07:33:38 | [36877289655](https://github.com/dreadnought-foundry/bureau-pipeline/actions/runs/36877289655) | classify, retry |
| 8 | 07:39:13 | Agent Plan | [36877848096](https://github.com/dreadnought-foundry/bureau-pipeline/actions/runs/36877848096/attempts/1) · 1 | 07:39:17 | [36878024458](https://github.com/dreadnought-foundry/bureau-pipeline/actions/runs/36878024458) | classify, retry |
| 9 | 07:42:46 | Groomer | [36878441139](https://github.com/dreadnought-foundry/bureau-pipeline/actions/runs/36878441139/attempts/1) · 1 | 07:42:49 | [36878502749](https://github.com/dreadnought-foundry/bureau-pipeline/actions/runs/36878502749) | classify, retry |
| 10 | 07:43:33 | Groomer | [36878441139](https://github.com/dreadnought-foundry/bureau-pipeline/actions/runs/36878441139/attempts/2) · 2 | 07:43:36 | [36878605059](https://github.com/dreadnought-foundry/bureau-pipeline/actions/runs/36878605059) | classify, diagnose |
| 11 | 07:45:07 | Agent Plan | [36878729528](https://github.com/dreadnought-foundry/bureau-pipeline/actions/runs/36878729528/attempts/1) · 1 | 07:45:13 | [36878817606](https://github.com/dreadnought-foundry/bureau-pipeline/actions/runs/36878817606) | classify, linear_rate_limited |
| 12 | 07:46:28 | Reconcile | [36878889008](https://github.com/dreadnought-foundry/bureau-pipeline/actions/runs/36878889008/attempts/1) · 1 | 07:46:32 | [36878993387](https://github.com/dreadnought-foundry/bureau-pipeline/actions/runs/36878993387) | classify, linear_rate_limited |
| 13 | 07:53:32 | Pipeline Tests | [36876784475](https://github.com/dreadnought-foundry/bureau-pipeline/actions/runs/36876784475/attempts/1) · 1 | 07:53:35 | [36879943643](https://github.com/dreadnought-foundry/bureau-pipeline/actions/runs/36879943643) | classify, linear_rate_limited |
| 14 | 07:55:13 | Agent Plan | [36880084889](https://github.com/dreadnought-foundry/bureau-pipeline/actions/runs/36880084889/attempts/1) · 1 | 07:55:16 | [36880170313](https://github.com/dreadnought-foundry/bureau-pipeline/actions/runs/36880170313) | classify, linear_rate_limited |
| 15 | 07:55:47 | Agent Task | [36875263935](https://github.com/dreadnought-foundry/bureau-pipeline/actions/runs/36875263935/attempts/1) · 1 | 07:55:51 | [36880247609](https://github.com/dreadnought-foundry/bureau-pipeline/actions/runs/36880247609) | classify, linear_rate_limited |
| 16 | 07:58:27 | Groomer | [36869130123](https://github.com/dreadnought-foundry/bureau-pipeline/actions/runs/36869130123/attempts/3) · 3 | 07:58:29 | [36880596632](https://github.com/dreadnought-foundry/bureau-pipeline/actions/runs/36880596632) | classify, diagnose |
| 17 | 08:06:29 | Reconcile | [36881466236](https://github.com/dreadnought-foundry/bureau-pipeline/actions/runs/36881466236/attempts/1) · 1 | 08:06:32 | [36881668130](https://github.com/dreadnought-foundry/bureau-pipeline/actions/runs/36881668130) | classify, upstream_outage |
| 18 | 08:21:37 | Groomer | [36883581050](https://github.com/dreadnought-foundry/bureau-pipeline/actions/runs/36883581050/attempts/1) · 1 | 08:21:39 | [36883634083](https://github.com/dreadnought-foundry/bureau-pipeline/actions/runs/36883634083) | classify, linear_rate_limited |
| 19 | 08:31:15 | Agent Plan | [36884770798](https://github.com/dreadnought-foundry/bureau-pipeline/actions/runs/36884770798/attempts/1) · 1 | 08:31:17 | [36884910305](https://github.com/dreadnought-foundry/bureau-pipeline/actions/runs/36884910305) | classify, linear_rate_limited |
| 20 | 09:18:38 | Agent Task | [36890946600](https://github.com/dreadnought-foundry/bureau-pipeline/actions/runs/36890946600/attempts/1) · 1 | 09:18:41 | [36891019910](https://github.com/dreadnought-foundry/bureau-pipeline/actions/runs/36891019910) | classify, linear_rate_limited |
| 21 | 09:18:41 | Agent Task | [36890942109](https://github.com/dreadnought-foundry/bureau-pipeline/actions/runs/36890942109/attempts/1) · 1 | 09:18:43 | [36891025150](https://github.com/dreadnought-foundry/bureau-pipeline/actions/runs/36891025150) | classify, linear_rate_limited |
| 22 | 09:28:49 | Agent Plan | [36892193259](https://github.com/dreadnought-foundry/bureau-pipeline/actions/runs/36892193259/attempts/1) · 1 | 09:28:51 | [36892308096](https://github.com/dreadnought-foundry/bureau-pipeline/actions/runs/36892308096) | classify, retry |
| 23 | 09:33:17 | Agent Plan | [36892741551](https://github.com/dreadnought-foundry/bureau-pipeline/actions/runs/36892741551/attempts/1) · 1 | 09:33:20 | [36892869984](https://github.com/dreadnought-foundry/bureau-pipeline/actions/runs/36892869984) | classify, linear_rate_limited |
| 24 | 09:42:38 | Agent Task | [36893907789](https://github.com/dreadnought-foundry/bureau-pipeline/actions/runs/36893907789/attempts/1) · 1 | 09:42:41 | [36894034664](https://github.com/dreadnought-foundry/bureau-pipeline/actions/runs/36894034664) | classify, linear_rate_limited |
| 25 | 09:54:51 | Agent Plan | [36895443916](https://github.com/dreadnought-foundry/bureau-pipeline/actions/runs/36895443916/attempts/1) · 1 | 09:54:54 | [36895526286](https://github.com/dreadnought-foundry/bureau-pipeline/actions/runs/36895526286) | classify, linear_rate_limited |
| 26 | 10:05:23 | Reconcile | [36896713695](https://github.com/dreadnought-foundry/bureau-pipeline/actions/runs/36896713695/attempts/1) · 1 | 10:05:26 | [36896803181](https://github.com/dreadnought-foundry/bureau-pipeline/actions/runs/36896803181) | classify, linear_rate_limited |
| 27 | 11:28:17 | Pipeline Tests | [36904131063](https://github.com/dreadnought-foundry/bureau-pipeline/actions/runs/36904131063/attempts/1) · 1 | 11:28:21 | [36907072656](https://github.com/dreadnought-foundry/bureau-pipeline/actions/runs/36907072656) | classify, linear_rate_limited |
| 28 | 11:58:30 | Agent Task | [36908930960](https://github.com/dreadnought-foundry/bureau-pipeline/actions/runs/36908930960/attempts/1) · 1 | 11:58:32 | [36910845828](https://github.com/dreadnought-foundry/bureau-pipeline/actions/runs/36910845828) | classify, linear_rate_limited |
| 29 | 12:22:25 | Pipeline Tests | [36910830564](https://github.com/dreadnought-foundry/bureau-pipeline/actions/runs/36910830564/attempts/1) · 1 | 12:22:28 | [36913806032](https://github.com/dreadnought-foundry/bureau-pipeline/actions/runs/36913806032) | classify, linear_rate_limited |
| 30 | 13:12:40 | Pipeline Tests | [36917061399](https://github.com/dreadnought-foundry/bureau-pipeline/actions/runs/36917061399/attempts/1) · 1 | 13:12:43 | [36919905331](https://github.com/dreadnought-foundry/bureau-pipeline/actions/runs/36919905331) | classify, linear_rate_limited |
| 31 | 13:52:36 | Reconcile | [36924682496](https://github.com/dreadnought-foundry/bureau-pipeline/actions/runs/36924682496/attempts/1) · 1 | 13:52:38 | [36924776721](https://github.com/dreadnought-foundry/bureau-pipeline/actions/runs/36924776721) | classify, linear_rate_limited |
| 32 | 13:53:00 | Agent Plan | [36924501759](https://github.com/dreadnought-foundry/bureau-pipeline/actions/runs/36924501759/attempts/1) · 1 | 13:53:03 | [36924825889](https://github.com/dreadnought-foundry/bureau-pipeline/actions/runs/36924825889) | classify, linear_rate_limited |
| 33 | 14:36:58 | Reconcile | [36929767669](https://github.com/dreadnought-foundry/bureau-pipeline/actions/runs/36929767669/attempts/1) · 1 | 14:37:00 | [36929852758](https://github.com/dreadnought-foundry/bureau-pipeline/actions/runs/36929852758) | classify, linear_rate_limited |
| 34 | 15:20:40 | Agent Plan | [36934423669](https://github.com/dreadnought-foundry/bureau-pipeline/actions/runs/36934423669/attempts/1) · 1 | 15:20:43 | [36934478744](https://github.com/dreadnought-foundry/bureau-pipeline/actions/runs/36934478744) | classify, linear_rate_limited |
| 35 | 15:23:53 | Reconcile | [36934757023](https://github.com/dreadnought-foundry/bureau-pipeline/actions/runs/36934757023/attempts/1) · 1 | 15:23:57 | [36934810296](https://github.com/dreadnought-foundry/bureau-pipeline/actions/runs/36934810296) | classify, linear_rate_limited |
| 36 | 15:35:25 | Reconcile | [36935915332](https://github.com/dreadnought-foundry/bureau-pipeline/actions/runs/36935915332/attempts/1) · 1 | 15:35:28 | [36935970194](https://github.com/dreadnought-foundry/bureau-pipeline/actions/runs/36935970194) | classify, linear_rate_limited |
| 37 | 15:48:30 | Reconcile | [36937164552](https://github.com/dreadnought-foundry/bureau-pipeline/actions/runs/36937164552/attempts/1) · 1 | 15:48:33 | [36937254104](https://github.com/dreadnought-foundry/bureau-pipeline/actions/runs/36937254104) | classify, linear_rate_limited |
| 38 | 15:58:08 | Linear Sync | [36937986308](https://github.com/dreadnought-foundry/bureau-pipeline/actions/runs/36937986308/attempts/1) · 1 | 15:58:11 | [36938166718](https://github.com/dreadnought-foundry/bureau-pipeline/actions/runs/36938166718) | classify, linear_rate_limited |
| 39 | 16:05:00 | Linear Sync | [36937986308](https://github.com/dreadnought-foundry/bureau-pipeline/actions/runs/36937986308/attempts/2) · 2 | 16:05:02 | [36938824969](https://github.com/dreadnought-foundry/bureau-pipeline/actions/runs/36938824969) | classify, linear_rate_limited |
| 40 | 16:47:30 | Reconcile | [36942619479](https://github.com/dreadnought-foundry/bureau-pipeline/actions/runs/36942619479/attempts/1) · 1 | 16:47:32 | [36942662525](https://github.com/dreadnought-foundry/bureau-pipeline/actions/runs/36942662525) | classify, linear_rate_limited |
| 41 | 17:19:07 | Pipeline Tests | [36943345477](https://github.com/dreadnought-foundry/bureau-pipeline/actions/runs/36943345477/attempts/1) · 1 | 17:19:09 | [36945380538](https://github.com/dreadnought-foundry/bureau-pipeline/actions/runs/36945380538) | classify, linear_rate_limited |
| 42 | 18:06:58 | Agent Task | [36949322868](https://github.com/dreadnought-foundry/bureau-pipeline/actions/runs/36949322868/attempts/1) · 1 | 18:07:01 | [36949376224](https://github.com/dreadnought-foundry/bureau-pipeline/actions/runs/36949376224) | classify, linear_rate_limited |
| 43 | 18:09:41 | Pipeline Tests | [36947477753](https://github.com/dreadnought-foundry/bureau-pipeline/actions/runs/36947477753/attempts/1) · 1 | 18:09:43 | [36949587624](https://github.com/dreadnought-foundry/bureau-pipeline/actions/runs/36949587624) | classify, linear_rate_limited |
| 44 | 18:59:21 | Linear Sync | [36953356990](https://github.com/dreadnought-foundry/bureau-pipeline/actions/runs/36953356990/attempts/1) · 1 | 18:59:23 | [36953509021](https://github.com/dreadnought-foundry/bureau-pipeline/actions/runs/36953509021) | classify, linear_rate_limited |
| 45 | 19:10:40 | Agent Plan | [36954247320](https://github.com/dreadnought-foundry/bureau-pipeline/actions/runs/36954247320/attempts/1) · 1 | 19:10:42 | [36954410301](https://github.com/dreadnought-foundry/bureau-pipeline/actions/runs/36954410301) | classify |
| 46 | 19:57:40 | Agent Plan | [36957949713](https://github.com/dreadnought-foundry/bureau-pipeline/actions/runs/36957949713/attempts/1) · 1 | 19:57:43 | [36958011108](https://github.com/dreadnought-foundry/bureau-pipeline/actions/runs/36958011108) | classify, retry |
| 47 | 21:52:16 | Split ledger | [36966240513](https://github.com/dreadnought-foundry/bureau-pipeline/actions/runs/36966240513/attempts/1) · 1 | 21:52:18 | [36966433554](https://github.com/dreadnought-foundry/bureau-pipeline/actions/runs/36966433554) | classify, linear_rate_limited |
| 48 | 22:05:32 | Reconcile | [36967343972](https://github.com/dreadnought-foundry/bureau-pipeline/actions/runs/36967343972/attempts/1) · 1 | 22:05:34 | [36967394915](https://github.com/dreadnought-foundry/bureau-pipeline/actions/runs/36967394915) | classify, linear_rate_limited |
| 49 | 22:13:29 | Reconcile | [36967940534](https://github.com/dreadnought-foundry/bureau-pipeline/actions/runs/36967940534/attempts/1) · 1 | 22:13:31 | [36967977381](https://github.com/dreadnought-foundry/bureau-pipeline/actions/runs/36967977381) | classify, linear_rate_limited |
| 50 | 22:13:41 | Agent Plan | [36967942387](https://github.com/dreadnought-foundry/bureau-pipeline/actions/runs/36967942387/attempts/1) · 1 | 22:13:44 | [36967992464](https://github.com/dreadnought-foundry/bureau-pipeline/actions/runs/36967992464) | classify, linear_rate_limited |

### agent-bureau — 106 failed attempts, 106 wakes

| # | failed (PT) | workflow | run · attempt | medic woke (PT) | medic run | jobs that ran |
| --: | -- | -- | -- | -- | -- | -- |
| 1 | 00:22:16 | CI | [36816466267](https://github.com/dreadnought-foundry/agent-bureau/actions/runs/36816466267/attempts/1) · 1 ¹ | 00:22:19 | [36829934183](https://github.com/dreadnought-foundry/agent-bureau/actions/runs/36829934183) | classify, retry |
| 2 | 00:38:27 | CI | [36814393949](https://github.com/dreadnought-foundry/agent-bureau/actions/runs/36814393949/attempts/2) · 2 ¹ | 00:38:29 | [36831496013](https://github.com/dreadnought-foundry/agent-bureau/actions/runs/36831496013) | classify, diagnose |
| 3 | 00:49:36 | CI | [36816241991](https://github.com/dreadnought-foundry/agent-bureau/actions/runs/36816241991/attempts/1) · 1 ¹ | 00:49:39 | [36832598191](https://github.com/dreadnought-foundry/agent-bureau/actions/runs/36832598191) | classify, retry |
| 4 | 00:49:54 | CI | [36816605952](https://github.com/dreadnought-foundry/agent-bureau/actions/runs/36816605952/attempts/1) · 1 ¹ | 00:49:56 | [36832626619](https://github.com/dreadnought-foundry/agent-bureau/actions/runs/36832626619) | classify, retry |
| 5 | 01:16:38 | CI | [36822019836](https://github.com/dreadnought-foundry/agent-bureau/actions/runs/36822019836/attempts/1) · 1 ¹ | 01:16:41 | [36835307093](https://github.com/dreadnought-foundry/agent-bureau/actions/runs/36835307093) | classify, retry |
| 6 | 01:28:51 | CI | [36816241991](https://github.com/dreadnought-foundry/agent-bureau/actions/runs/36816241991/attempts/2) · 2 ¹ | 01:28:54 | [36836564641](https://github.com/dreadnought-foundry/agent-bureau/actions/runs/36836564641) | classify, diagnose |
| 7 | 01:31:42 | CI | [36816605952](https://github.com/dreadnought-foundry/agent-bureau/actions/runs/36816605952/attempts/2) · 2 ¹ | 01:31:45 | [36836869453](https://github.com/dreadnought-foundry/agent-bureau/actions/runs/36836869453) | classify, diagnose |
| 8 | 01:34:08 | CI | [36816466267](https://github.com/dreadnought-foundry/agent-bureau/actions/runs/36816466267/attempts/2) · 2 ¹ | 01:34:10 | [36837123201](https://github.com/dreadnought-foundry/agent-bureau/actions/runs/36837123201) | classify, diagnose |
| 9 | 01:49:43 | CI | [36820809682](https://github.com/dreadnought-foundry/agent-bureau/actions/runs/36820809682/attempts/1) · 1 ¹ | 01:49:46 | [36838777192](https://github.com/dreadnought-foundry/agent-bureau/actions/runs/36838777192) | classify, retry |
| 10 | 01:55:12 | CI | [36824615160](https://github.com/dreadnought-foundry/agent-bureau/actions/runs/36824615160/attempts/1) · 1 ¹ | 01:55:15 | [36839362213](https://github.com/dreadnought-foundry/agent-bureau/actions/runs/36839362213) | classify, retry |
| 11 | 01:55:25 | CI | [36822019836](https://github.com/dreadnought-foundry/agent-bureau/actions/runs/36822019836/attempts/2) · 2 ¹ | 01:55:28 | [36839386893](https://github.com/dreadnought-foundry/agent-bureau/actions/runs/36839386893) | classify, diagnose |
| 12 | 02:10:02 | CI | [36826067273](https://github.com/dreadnought-foundry/agent-bureau/actions/runs/36826067273/attempts/1) · 1 ¹ | 02:10:04 | [36840951841](https://github.com/dreadnought-foundry/agent-bureau/actions/runs/36840951841) | classify, retry |
| 13 | 02:14:28 | CI | [36820809682](https://github.com/dreadnought-foundry/agent-bureau/actions/runs/36820809682/attempts/2) · 2 ¹ | 02:14:30 | [36841424566](https://github.com/dreadnought-foundry/agent-bureau/actions/runs/36841424566) | classify, diagnose |
| 14 | 02:15:00 | CI | [36826067273](https://github.com/dreadnought-foundry/agent-bureau/actions/runs/36826067273/attempts/2) · 2 ¹ | 02:15:03 | [36841484911](https://github.com/dreadnought-foundry/agent-bureau/actions/runs/36841484911) | classify, diagnose |
| 15 | 02:15:12 | CI | [36824615160](https://github.com/dreadnought-foundry/agent-bureau/actions/runs/36824615160/attempts/2) · 2 ¹ | 02:15:16 | [36841510543](https://github.com/dreadnought-foundry/agent-bureau/actions/runs/36841510543) | classify, diagnose |
| 16 | 02:25:25 | Agent Task | [36840981732](https://github.com/dreadnought-foundry/agent-bureau/actions/runs/36840981732/attempts/1) · 1 | 02:25:27 | [36842632457](https://github.com/dreadnought-foundry/agent-bureau/actions/runs/36842632457) | classify, retry |
| 17 | 02:31:41 | CI | [36837670624](https://github.com/dreadnought-foundry/agent-bureau/actions/runs/36837670624/attempts/1) · 1 | 02:31:44 | [36843318087](https://github.com/dreadnought-foundry/agent-bureau/actions/runs/36843318087) | classify, retry_declined |
| 18 | 04:11:42 | Agent Task | [36851649612](https://github.com/dreadnought-foundry/agent-bureau/actions/runs/36851649612/attempts/1) · 1 | 04:11:45 | [36853835270](https://github.com/dreadnought-foundry/agent-bureau/actions/runs/36853835270) | classify, retry |
| 19 | 04:13:41 | Agent Task | [36851655350](https://github.com/dreadnought-foundry/agent-bureau/actions/runs/36851655350/attempts/1) · 1 | 04:13:44 | [36854045283](https://github.com/dreadnought-foundry/agent-bureau/actions/runs/36854045283) | classify, retry |
| 20 | 04:14:41 | Agent Task | [36851651588](https://github.com/dreadnought-foundry/agent-bureau/actions/runs/36851651588/attempts/1) · 1 | 04:14:44 | [36854148294](https://github.com/dreadnought-foundry/agent-bureau/actions/runs/36854148294) | classify, retry |
| 21 | 04:23:05 | QA Review | [36853438680](https://github.com/dreadnought-foundry/agent-bureau/actions/runs/36853438680/attempts/1) · 1 | 04:23:07 | [36855030102](https://github.com/dreadnought-foundry/agent-bureau/actions/runs/36855030102) | classify, backoff |
| 22 | 05:40:21 | CI | [36853814842](https://github.com/dreadnought-foundry/agent-bureau/actions/runs/36853814842/attempts/1) · 1 | 05:40:26 | [36863332016](https://github.com/dreadnought-foundry/agent-bureau/actions/runs/36863332016) | classify, retry |
| 23 | 06:15:14 | CI | [36866907643](https://github.com/dreadnought-foundry/agent-bureau/actions/runs/36866907643/attempts/1) · 1 | 06:15:17 | [36867343254](https://github.com/dreadnought-foundry/agent-bureau/actions/runs/36867343254) | classify, retry |
| 24 | 06:16:04 | CI | [36855687707](https://github.com/dreadnought-foundry/agent-bureau/actions/runs/36855687707/attempts/1) · 1 | 06:16:07 | [36867445198](https://github.com/dreadnought-foundry/agent-bureau/actions/runs/36867445198) | classify, retry |
| 25 | 06:19:27 | CI | [36855687707](https://github.com/dreadnought-foundry/agent-bureau/actions/runs/36855687707/attempts/2) · 2 | 06:19:30 | [36867845792](https://github.com/dreadnought-foundry/agent-bureau/actions/runs/36867845792) | classify, diagnose |
| 26 | 06:21:22 | CI | [36853814842](https://github.com/dreadnought-foundry/agent-bureau/actions/runs/36853814842/attempts/2) · 2 | 06:21:26 | [36868086570](https://github.com/dreadnought-foundry/agent-bureau/actions/runs/36868086570) | classify, diagnose |
| 27 | 07:29:50 | Agent Task | [36874875491](https://github.com/dreadnought-foundry/agent-bureau/actions/runs/36874875491/attempts/1) · 1 | 07:29:53 | [36876787210](https://github.com/dreadnought-foundry/agent-bureau/actions/runs/36876787210) | classify, retry |
| 28 | 07:29:52 | Agent Task | [36874879915](https://github.com/dreadnought-foundry/agent-bureau/actions/runs/36874879915/attempts/1) · 1 | 07:29:55 | [36876791953](https://github.com/dreadnought-foundry/agent-bureau/actions/runs/36876791953) | classify, retry |
| 29 | 07:34:08 | CI | [36876766060](https://github.com/dreadnought-foundry/agent-bureau/actions/runs/36876766060/attempts/1) · 1 | 07:34:11 | [36877360504](https://github.com/dreadnought-foundry/agent-bureau/actions/runs/36877360504) | classify, retry |
| 30 | 07:39:44 | CI | [36876766060](https://github.com/dreadnought-foundry/agent-bureau/actions/runs/36876766060/attempts/2) · 2 | 07:39:47 | [36878092098](https://github.com/dreadnought-foundry/agent-bureau/actions/runs/36878092098) | classify, diagnose |
| 31 | 07:40:42 | Agent Plan | [36878100703](https://github.com/dreadnought-foundry/agent-bureau/actions/runs/36878100703/attempts/1) · 1 | 07:40:46 | [36878229222](https://github.com/dreadnought-foundry/agent-bureau/actions/runs/36878229222) | classify, linear_rate_limited |
| 32 | 07:45:24 | Agent Plan | [36878666642](https://github.com/dreadnought-foundry/agent-bureau/actions/runs/36878666642/attempts/1) · 1 | 07:45:27 | [36878850297](https://github.com/dreadnought-foundry/agent-bureau/actions/runs/36878850297) | classify, linear_rate_limited |
| 33 | 07:46:55 | Linear Sync | [36878774753](https://github.com/dreadnought-foundry/agent-bureau/actions/runs/36878774753/attempts/1) · 1 | 07:46:59 | [36879054023](https://github.com/dreadnought-foundry/agent-bureau/actions/runs/36879054023) | classify, linear_rate_limited |
| 34 | 07:48:20 | Linear Sync | [36878963353](https://github.com/dreadnought-foundry/agent-bureau/actions/runs/36878963353/attempts/1) · 1 | 07:48:23 | [36879243095](https://github.com/dreadnought-foundry/agent-bureau/actions/runs/36879243095) | classify, linear_rate_limited |
| 35 | 08:16:16 | Agent Plan | [36882647096](https://github.com/dreadnought-foundry/agent-bureau/actions/runs/36882647096/attempts/1) · 1 | 08:16:20 | [36882923192](https://github.com/dreadnought-foundry/agent-bureau/actions/runs/36882923192) | classify, retry |
| 36 | 08:19:34 | Agent Plan | [36883021613](https://github.com/dreadnought-foundry/agent-bureau/actions/runs/36883021613/attempts/1) · 1 | 08:19:37 | [36883364734](https://github.com/dreadnought-foundry/agent-bureau/actions/runs/36883364734) | classify, retry |
| 37 | 08:19:59 | Agent Plan | [36880678774](https://github.com/dreadnought-foundry/agent-bureau/actions/runs/36880678774/attempts/1) · 1 | 08:20:02 | [36883419519](https://github.com/dreadnought-foundry/agent-bureau/actions/runs/36883419519) | classify, linear_rate_limited |
| 38 | 08:20:33 | Agent Plan | [36883021613](https://github.com/dreadnought-foundry/agent-bureau/actions/runs/36883021613/attempts/2) · 2 | 08:20:37 | [36883498083](https://github.com/dreadnought-foundry/agent-bureau/actions/runs/36883498083) | classify, linear_rate_limited |
| 39 | 08:31:11 | Agent Plan | [36884679913](https://github.com/dreadnought-foundry/agent-bureau/actions/runs/36884679913/attempts/1) · 1 | 08:31:14 | [36884904257](https://github.com/dreadnought-foundry/agent-bureau/actions/runs/36884904257) | classify, linear_rate_limited |
| 40 | 08:31:15 | Agent Plan | [36884683142](https://github.com/dreadnought-foundry/agent-bureau/actions/runs/36884683142/attempts/1) · 1 | 08:31:19 | [36884913122](https://github.com/dreadnought-foundry/agent-bureau/actions/runs/36884913122) | classify, linear_rate_limited |
| 41 | 08:32:13 | Agent Plan | [36884448561](https://github.com/dreadnought-foundry/agent-bureau/actions/runs/36884448561/attempts/1) · 1 | 08:32:16 | [36885039235](https://github.com/dreadnought-foundry/agent-bureau/actions/runs/36885039235) | classify, linear_rate_limited |
| 42 | 08:33:55 | Linear Sync | [36884988074](https://github.com/dreadnought-foundry/agent-bureau/actions/runs/36884988074/attempts/1) · 1 | 08:33:59 | [36885256392](https://github.com/dreadnought-foundry/agent-bureau/actions/runs/36885256392) | classify, linear_rate_limited |
| 43 | 08:37:44 | Linear Sync | [36884825091](https://github.com/dreadnought-foundry/agent-bureau/actions/runs/36884825091/attempts/1) · 1 | 08:37:48 | [36885754273](https://github.com/dreadnought-foundry/agent-bureau/actions/runs/36885754273) | classify, linear_rate_limited |
| 44 | 08:51:50 | Agent Plan | [36887445298](https://github.com/dreadnought-foundry/agent-bureau/actions/runs/36887445298/attempts/1) · 1 | 08:51:53 | [36887577314](https://github.com/dreadnought-foundry/agent-bureau/actions/runs/36887577314) | classify, linear_rate_limited |
| 45 | 08:56:29 | CI | [36884987750](https://github.com/dreadnought-foundry/agent-bureau/actions/runs/36884987750/attempts/1) · 1 | 08:56:33 | [36888185808](https://github.com/dreadnought-foundry/agent-bureau/actions/runs/36888185808) | classify, retry |
| 46 | 08:56:52 | Agent Plan | [36887655090](https://github.com/dreadnought-foundry/agent-bureau/actions/runs/36887655090/attempts/1) · 1 | 08:56:56 | [36888234353](https://github.com/dreadnought-foundry/agent-bureau/actions/runs/36888234353) | classify, linear_rate_limited |
| 47 | 09:14:57 | Agent Plan | [36890456358](https://github.com/dreadnought-foundry/agent-bureau/actions/runs/36890456358/attempts/1) · 1 | 09:15:00 | [36890556939](https://github.com/dreadnought-foundry/agent-bureau/actions/runs/36890556939) | classify, linear_rate_limited |
| 48 | 09:14:58 | Agent Plan | [36890463469](https://github.com/dreadnought-foundry/agent-bureau/actions/runs/36890463469/attempts/1) · 1 | 09:15:00 | [36890557728](https://github.com/dreadnought-foundry/agent-bureau/actions/runs/36890557728) | classify, linear_rate_limited, retry_declined |
| 49 | 09:15:45 | Agent Plan | [36890514223](https://github.com/dreadnought-foundry/agent-bureau/actions/runs/36890514223/attempts/1) · 1 | 09:15:47 | [36890660069](https://github.com/dreadnought-foundry/agent-bureau/actions/runs/36890660069) | classify, linear_rate_limited |
| 50 | 09:16:44 | Linear Sync | [36890521845](https://github.com/dreadnought-foundry/agent-bureau/actions/runs/36890521845/attempts/1) · 1 | 09:16:47 | [36890786723](https://github.com/dreadnought-foundry/agent-bureau/actions/runs/36890786723) | classify, linear_rate_limited |
| 51 | 09:42:37 | Agent Plan | [36893469343](https://github.com/dreadnought-foundry/agent-bureau/actions/runs/36893469343/attempts/1) · 1 | 09:42:40 | [36894032592](https://github.com/dreadnought-foundry/agent-bureau/actions/runs/36894032592) | classify, linear_rate_limited |
| 52 | 09:42:40 | Agent Plan | [36893921514](https://github.com/dreadnought-foundry/agent-bureau/actions/runs/36893921514/attempts/1) · 1 | 09:42:42 | [36894037505](https://github.com/dreadnought-foundry/agent-bureau/actions/runs/36894037505) | classify, linear_rate_limited |
| 53 | 09:55:42 | Agent Plan | [36894950716](https://github.com/dreadnought-foundry/agent-bureau/actions/runs/36894950716/attempts/1) · 1 | 09:55:45 | [36895629180](https://github.com/dreadnought-foundry/agent-bureau/actions/runs/36895629180) | classify, linear_rate_limited |
| 54 | 09:56:35 | Agent Plan | [36895608244](https://github.com/dreadnought-foundry/agent-bureau/actions/runs/36895608244/attempts/1) · 1 | 09:56:38 | [36895729729](https://github.com/dreadnought-foundry/agent-bureau/actions/runs/36895729729) | classify, linear_rate_limited |
| 55 | 10:06:28 | Agent Plan | [36896791927](https://github.com/dreadnought-foundry/agent-bureau/actions/runs/36896791927/attempts/1) · 1 | 10:06:31 | [36896938841](https://github.com/dreadnought-foundry/agent-bureau/actions/runs/36896938841) | classify, linear_rate_limited |
| 56 | 10:44:29 | Agent Plan | [36900705521](https://github.com/dreadnought-foundry/agent-bureau/actions/runs/36900705521/attempts/1) · 1 | 10:44:32 | [36901614690](https://github.com/dreadnought-foundry/agent-bureau/actions/runs/36901614690) | classify |
| 57 | 11:40:33 | Agent Task | [36900732578](https://github.com/dreadnought-foundry/agent-bureau/actions/runs/36900732578/attempts/1) · 1 | 11:40:36 | [36908600123](https://github.com/dreadnought-foundry/agent-bureau/actions/runs/36908600123) | classify, retry |
| 58 | 12:05:23 | Agent Plan | [36909472396](https://github.com/dreadnought-foundry/agent-bureau/actions/runs/36909472396/attempts/1) · 1 | 12:05:27 | [36911705313](https://github.com/dreadnought-foundry/agent-bureau/actions/runs/36911705313) | classify |
| 59 | 12:06:02 | Agent Plan | [36911678502](https://github.com/dreadnought-foundry/agent-bureau/actions/runs/36911678502/attempts/1) · 1 | 12:06:05 | [36911784453](https://github.com/dreadnought-foundry/agent-bureau/actions/runs/36911784453) | classify, linear_rate_limited |
| 60 | 12:06:39 | Agent Plan | [36910616024](https://github.com/dreadnought-foundry/agent-bureau/actions/runs/36910616024/attempts/1) · 1 | 12:06:42 | [36911860030](https://github.com/dreadnought-foundry/agent-bureau/actions/runs/36911860030) | classify, linear_rate_limited |
| 61 | 13:23:03 | Agent Plan | [36920487413](https://github.com/dreadnought-foundry/agent-bureau/actions/runs/36920487413/attempts/1) · 1 | 13:23:07 | [36921170040](https://github.com/dreadnought-foundry/agent-bureau/actions/runs/36921170040) | classify, linear_rate_limited |
| 62 | 13:44:29 | Agent Plan | [36923693192](https://github.com/dreadnought-foundry/agent-bureau/actions/runs/36923693192/attempts/1) · 1 | 13:44:32 | [36923793259](https://github.com/dreadnought-foundry/agent-bureau/actions/runs/36923793259) | classify, linear_rate_limited |
| 63 | 13:45:06 | Agent Task | [36923766223](https://github.com/dreadnought-foundry/agent-bureau/actions/runs/36923766223/attempts/1) · 1 | 13:45:09 | [36923866018](https://github.com/dreadnought-foundry/agent-bureau/actions/runs/36923866018) | classify, linear_rate_limited |
| 64 | 13:45:08 | Agent Task | [36923778658](https://github.com/dreadnought-foundry/agent-bureau/actions/runs/36923778658/attempts/1) · 1 | 13:45:12 | [36923871959](https://github.com/dreadnought-foundry/agent-bureau/actions/runs/36923871959) | classify, linear_rate_limited |
| 65 | 13:45:15 | Agent Task | [36923773002](https://github.com/dreadnought-foundry/agent-bureau/actions/runs/36923773002/attempts/1) · 1 | 13:45:18 | [36923885758](https://github.com/dreadnought-foundry/agent-bureau/actions/runs/36923885758) | classify, linear_rate_limited |
| 66 | 13:53:09 | Agent Task | [36924711071](https://github.com/dreadnought-foundry/agent-bureau/actions/runs/36924711071/attempts/1) · 1 | 13:53:13 | [36924844504](https://github.com/dreadnought-foundry/agent-bureau/actions/runs/36924844504) | classify, linear_rate_limited |
| 67 | 14:15:18 | Agent Plan | [36926906506](https://github.com/dreadnought-foundry/agent-bureau/actions/runs/36926906506/attempts/1) · 1 | 14:15:23 | [36927425128](https://github.com/dreadnought-foundry/agent-bureau/actions/runs/36927425128) | classify, linear_rate_limited |
| 68 | 14:37:25 | Agent Plan | [36929819535](https://github.com/dreadnought-foundry/agent-bureau/actions/runs/36929819535/attempts/1) · 1 | 14:37:29 | [36929904034](https://github.com/dreadnought-foundry/agent-bureau/actions/runs/36929904034) | classify, linear_rate_limited |
| 69 | 14:37:47 | Agent Task | [36929841500](https://github.com/dreadnought-foundry/agent-bureau/actions/runs/36929841500/attempts/1) · 1 | 14:37:51 | [36929941789](https://github.com/dreadnought-foundry/agent-bureau/actions/runs/36929941789) | classify, linear_rate_limited |
| 70 | 14:49:50 | Agent Plan | [36931185130](https://github.com/dreadnought-foundry/agent-bureau/actions/runs/36931185130/attempts/1) · 1 | 14:49:52 | [36931245351](https://github.com/dreadnought-foundry/agent-bureau/actions/runs/36931245351) | classify, linear_rate_limited |
| 71 | 14:50:04 | Agent Task | [36931197997](https://github.com/dreadnought-foundry/agent-bureau/actions/runs/36931197997/attempts/1) · 1 | 14:50:08 | [36931273360](https://github.com/dreadnought-foundry/agent-bureau/actions/runs/36931273360) | classify, linear_rate_limited |
| 72 | 14:50:07 | Agent Task | [36931197412](https://github.com/dreadnought-foundry/agent-bureau/actions/runs/36931197412/attempts/1) · 1 | 14:50:09 | [36931276367](https://github.com/dreadnought-foundry/agent-bureau/actions/runs/36931276367) | classify, linear_rate_limited |
| 73 | 14:50:08 | Agent Task | [36931206984](https://github.com/dreadnought-foundry/agent-bureau/actions/runs/36931206984/attempts/1) · 1 | 14:50:11 | [36931279387](https://github.com/dreadnought-foundry/agent-bureau/actions/runs/36931279387) | classify, linear_rate_limited |
| 74 | 14:50:11 | Agent Task | [36931208456](https://github.com/dreadnought-foundry/agent-bureau/actions/runs/36931208456/attempts/1) · 1 | 14:50:13 | [36931284480](https://github.com/dreadnought-foundry/agent-bureau/actions/runs/36931284480) | classify, linear_rate_limited |
| 75 | 14:50:29 | Agent Task | [36931223042](https://github.com/dreadnought-foundry/agent-bureau/actions/runs/36931223042/attempts/1) · 1 | 14:50:31 | [36931318639](https://github.com/dreadnought-foundry/agent-bureau/actions/runs/36931318639) | classify, linear_rate_limited |
| 76 | 15:18:30 | Agent Task | [36934165232](https://github.com/dreadnought-foundry/agent-bureau/actions/runs/36934165232/attempts/1) · 1 | 15:18:33 | [36934256687](https://github.com/dreadnought-foundry/agent-bureau/actions/runs/36934256687) | classify, linear_rate_limited |
| 77 | 15:20:42 | Linear Sync | [36934235087](https://github.com/dreadnought-foundry/agent-bureau/actions/runs/36934235087/attempts/1) · 1 | 15:20:45 | [36934481877](https://github.com/dreadnought-foundry/agent-bureau/actions/runs/36934481877) | classify, linear_rate_limited |
| 78 | 15:20:43 | Agent Plan | [36932447649](https://github.com/dreadnought-foundry/agent-bureau/actions/runs/36932447649/attempts/1) · 1 | 15:20:45 | [36934482359](https://github.com/dreadnought-foundry/agent-bureau/actions/runs/36934482359) | classify |
| 79 | 15:20:57 | Agent Plan | [36934449513](https://github.com/dreadnought-foundry/agent-bureau/actions/runs/36934449513/attempts/1) · 1 | 15:21:00 | [36934507350](https://github.com/dreadnought-foundry/agent-bureau/actions/runs/36934507350) | classify, linear_rate_limited |
| 80 | 15:22:08 | Agent Plan | [36934573972](https://github.com/dreadnought-foundry/agent-bureau/actions/runs/36934573972/attempts/1) · 1 | 15:22:11 | [36934629526](https://github.com/dreadnought-foundry/agent-bureau/actions/runs/36934629526) | classify, linear_rate_limited |
| 81 | 15:22:27 | Agent Plan | [36934571562](https://github.com/dreadnought-foundry/agent-bureau/actions/runs/36934571562/attempts/1) · 1 | 15:22:31 | [36934662754](https://github.com/dreadnought-foundry/agent-bureau/actions/runs/36934662754) | classify, linear_rate_limited |
| 82 | 15:22:44 | Linear Sync | [36934479771](https://github.com/dreadnought-foundry/agent-bureau/actions/runs/36934479771/attempts/1) · 1 | 15:22:47 | [36934689946](https://github.com/dreadnought-foundry/agent-bureau/actions/runs/36934689946) | classify, linear_rate_limited |
| 83 | 15:25:54 | Agent Plan | [36934581657](https://github.com/dreadnought-foundry/agent-bureau/actions/runs/36934581657/attempts/1) · 1 | 15:25:57 | [36935018140](https://github.com/dreadnought-foundry/agent-bureau/actions/runs/36935018140) | classify, linear_rate_limited |
| 84 | 15:48:37 | Linear Sync | [36937068320](https://github.com/dreadnought-foundry/agent-bureau/actions/runs/36937068320/attempts/1) · 1 | 15:48:40 | [36937265926](https://github.com/dreadnought-foundry/agent-bureau/actions/runs/36937265926) | classify, linear_rate_limited |
| 85 | 15:55:24 | Linear Sync | [36937722337](https://github.com/dreadnought-foundry/agent-bureau/actions/runs/36937722337/attempts/1) · 1 | 15:55:27 | [36937916565](https://github.com/dreadnought-foundry/agent-bureau/actions/runs/36937916565) | classify, linear_rate_limited |
| 86 | 16:05:22 | Agent Task | [36938801049](https://github.com/dreadnought-foundry/agent-bureau/actions/runs/36938801049/attempts/1) · 1 | 16:05:25 | [36938861831](https://github.com/dreadnought-foundry/agent-bureau/actions/runs/36938861831) | classify, linear_rate_limited |
| 87 | 16:05:26 | Agent Plan | [36938787843](https://github.com/dreadnought-foundry/agent-bureau/actions/runs/36938787843/attempts/1) · 1 | 16:05:29 | [36938868213](https://github.com/dreadnought-foundry/agent-bureau/actions/runs/36938868213) | classify, linear_rate_limited |
| 88 | 16:44:01 | Agent Plan | [36942291006](https://github.com/dreadnought-foundry/agent-bureau/actions/runs/36942291006/attempts/1) · 1 | 16:44:04 | [36942351324](https://github.com/dreadnought-foundry/agent-bureau/actions/runs/36942351324) | classify, linear_rate_limited |
| 89 | 16:50:15 | Agent Plan | [36942127077](https://github.com/dreadnought-foundry/agent-bureau/actions/runs/36942127077/attempts/1) · 1 | 16:50:18 | [36942903710](https://github.com/dreadnought-foundry/agent-bureau/actions/runs/36942903710) | classify, linear_rate_limited |
| 90 | 17:30:27 | Agent Plan | [36942291006](https://github.com/dreadnought-foundry/agent-bureau/actions/runs/36942291006/attempts/2) · 2 | 17:30:30 | [36946337762](https://github.com/dreadnought-foundry/agent-bureau/actions/runs/36946337762) | classify, linear_rate_limited |
| 91 | 17:30:54 | Agent Task | [36946319237](https://github.com/dreadnought-foundry/agent-bureau/actions/runs/36946319237/attempts/1) · 1 | 17:30:57 | [36946379637](https://github.com/dreadnought-foundry/agent-bureau/actions/runs/36946379637) | classify, linear_rate_limited |
| 92 | 17:31:33 | Agent Plan | [36944981635](https://github.com/dreadnought-foundry/agent-bureau/actions/runs/36944981635/attempts/1) · 1 | 17:31:36 | [36946433348](https://github.com/dreadnought-foundry/agent-bureau/actions/runs/36946433348) | classify, linear_rate_limited |
| 93 | 17:31:38 | Linear Sync | [36946262185](https://github.com/dreadnought-foundry/agent-bureau/actions/runs/36946262185/attempts/1) · 1 | 17:31:41 | [36946440931](https://github.com/dreadnought-foundry/agent-bureau/actions/runs/36946440931) | classify, linear_rate_limited |
| 94 | 17:42:07 | Agent Plan | [36947229490](https://github.com/dreadnought-foundry/agent-bureau/actions/runs/36947229490/attempts/1) · 1 | 17:42:09 | [36947308804](https://github.com/dreadnought-foundry/agent-bureau/actions/runs/36947308804) | classify, retry |
| 95 | 18:21:30 | Agent Plan | [36950032919](https://github.com/dreadnought-foundry/agent-bureau/actions/runs/36950032919/attempts/1) · 1 | 18:21:33 | [36950548189](https://github.com/dreadnought-foundry/agent-bureau/actions/runs/36950548189) | classify, linear_rate_limited |
| 96 | 18:54:51 | Agent Plan | [36953093853](https://github.com/dreadnought-foundry/agent-bureau/actions/runs/36953093853/attempts/1) · 1 | 18:54:54 | [36953174391](https://github.com/dreadnought-foundry/agent-bureau/actions/runs/36953174391) | classify, linear_rate_limited |
| 97 | 19:30:35 | Agent Plan | [36955459907](https://github.com/dreadnought-foundry/agent-bureau/actions/runs/36955459907/attempts/1) · 1 | 19:30:38 | [36955960091](https://github.com/dreadnought-foundry/agent-bureau/actions/runs/36955960091) | classify |
| 98 | 21:33:11 | Agent Plan | [36964491515](https://github.com/dreadnought-foundry/agent-bureau/actions/runs/36964491515/attempts/1) · 1 | 21:33:13 | [36965052647](https://github.com/dreadnought-foundry/agent-bureau/actions/runs/36965052647) | classify |
| 99 | 21:35:17 | Agent Plan | [36965035541](https://github.com/dreadnought-foundry/agent-bureau/actions/runs/36965035541/attempts/1) · 1 | 21:35:19 | [36965207790](https://github.com/dreadnought-foundry/agent-bureau/actions/runs/36965207790) | classify, retry |
| 100 | 22:09:24 | Verify | [36963604977](https://github.com/dreadnought-foundry/agent-bureau/actions/runs/36963604977/attempts/1) · 1 | 22:09:27 | [36967679374](https://github.com/dreadnought-foundry/agent-bureau/actions/runs/36967679374) | classify, retry |
| 101 | 22:14:45 | Agent Plan | [36966144944](https://github.com/dreadnought-foundry/agent-bureau/actions/runs/36966144944/attempts/1) · 1 | 22:14:48 | [36968072056](https://github.com/dreadnought-foundry/agent-bureau/actions/runs/36968072056) | classify |
| 102 | 22:34:29 | Verify | [36963604977](https://github.com/dreadnought-foundry/agent-bureau/actions/runs/36963604977/attempts/2) · 2 | 22:34:31 | [36969548914](https://github.com/dreadnought-foundry/agent-bureau/actions/runs/36969548914) | classify, diagnose |
| 103 | 22:45:41 | Agent Plan | [36970160528](https://github.com/dreadnought-foundry/agent-bureau/actions/runs/36970160528/attempts/1) · 1 | 22:45:44 | [36970391705](https://github.com/dreadnought-foundry/agent-bureau/actions/runs/36970391705) | classify, linear_rate_limited |
| 104 | 22:45:42 | Agent Plan | [36970167981](https://github.com/dreadnought-foundry/agent-bureau/actions/runs/36970167981/attempts/1) · 1 | 22:45:45 | [36970393419](https://github.com/dreadnought-foundry/agent-bureau/actions/runs/36970393419) | classify, linear_rate_limited |
| 105 | 22:46:25 | Agent Plan | [36970172862](https://github.com/dreadnought-foundry/agent-bureau/actions/runs/36970172862/attempts/1) · 1 | 22:46:28 | [36970447664](https://github.com/dreadnought-foundry/agent-bureau/actions/runs/36970447664) | classify, linear_rate_limited |
| 106 | 22:57:59 | Agent Plan | [36969981086](https://github.com/dreadnought-foundry/agent-bureau/actions/runs/36969981086/attempts/1) · 1 | 22:58:01 | [36971311096](https://github.com/dreadnought-foundry/agent-bureau/actions/runs/36971311096) | classify, linear_rate_limited |
