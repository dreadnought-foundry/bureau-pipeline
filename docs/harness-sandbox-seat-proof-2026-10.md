# The harness driver reads Linear as `bureau-sandbox` — observed 2026-10-01 and 2026-10-02

The proof for [DRE-5451](https://linear.app/dreadnoughtfoundry/issue/DRE-5451),
under epic [DRE-3650](https://linear.app/dreadnoughtfoundry/issue/DRE-3650).
Every time below is Pacific (PDT, UTC−7) and comes from the event's own clock:
a run's `createdAt` or `updatedAt`, a log line's timestamp, or the moment a
command ran.

The claim under proof is the epic's headline: once
[DRE-3651](https://linear.app/dreadnoughtfoundry/issue/DRE-3651) is on `main`,
this repo's Integration Harness driver makes its one Linear read per run as
`bureau-sandbox`, on the sandbox's own hour, and a proving run on `main` no
longer spends the fleet's 2,500-requests-per-hour bucket.

**Who observed what.** Section 1 was read by hand by the operator on
2026-10-01 at 21:05 PT and posted as a comment on DRE-5451; it is copied here
unchanged, not re-taken. Section 2 and the closing table were observed and
written by an agent session on 2026-10-02, on the CEO's instruction of about
16:30 PT that day to run the bureau-pipeline proof cards waiting in Green Light.

**Summary: the claim held.** The first `main` run after the merge ended
`budget: sandbox` and `stable` moved past it (§1). A second `main` run,
bracketed by header reads of both keys, read Linear at 2,499 (the sandbox's
number) while the fleet's bucket stood between 1,221 and 1,436. No fleet
spend line in the window belongs to the harness (§2). Of DRE-3651's five
criteria, four held and one did not: the pull request body never named the
post-merge observation. Its body was written by the push-rescue step, not by
the agent (§3).

---

## 1. The first `main` run reads as the sandbox — operator, 2026-10-01

Copied from the operator's comment on DRE-5451 (posted 2026-10-01 21:06 PT).

- DRE-3651 merged as bureau-pipeline PR
  [#636](https://github.com/dreadnought-foundry/bureau-pipeline/pull/636),
  merge commit `73bb2be1925b366cc0c6732c3e6c0551eb56eeab`, at **17:50:31 PT**
  on 2026-10-01.
- **The first `Integration Harness` run on `main` at or after that commit is
  run [36948008740](https://github.com/dreadnought-foundry/bureau-pipeline/actions/runs/36948008740)**
  (push on `73bb2be19`, started 17:50:34 PT, concluded **success** at
  17:59:15 PT). The run before it,
  [36945955140](https://github.com/dreadnought-foundry/bureau-pipeline/actions/runs/36945955140),
  ran on `b2c04bcb6`, which is before the merge.
- Its `Run harness scenarios` step's `linear-budget:` line, verbatim
  (17:59:07 PT):

  ```
  linear-budget: 2499 → 2499 (spent 0 this run; window resets 18:59 PT; budget: sandbox)
  ```

- `git ls-remote origin stable` at 21:05 PT returned
  `30d51fe34fc3b44ee0b8da906b9255bfe131bf0f`. The compare
  `73bb2be…...stable` reads `ahead`, 9 commits, so `stable` advanced past the
  merge. A missing secret would have stopped that. Three later harness runs
  on `main`
  ([36950258005](https://github.com/dreadnought-foundry/bureau-pipeline/actions/runs/36950258005),
  [36952824974](https://github.com/dreadnought-foundry/bureau-pipeline/actions/runs/36952824974),
  [36953356685](https://github.com/dreadnought-foundry/bureau-pipeline/actions/runs/36953356685))
  also succeeded.

None of those runs was bracketed by header reads, so §2 is a separate,
hand-dispatched run.

## 2. The fleet's hour does not move for it — 2026-10-02, 16:37:49 to 16:46:01 PT

### 2.1 How the headers were read

Both keys were read from the operator's `.env` (the agent-bureau checkout),
`LINEAR_API_KEY_FLEET` and `LINEAR_API_KEY_SANDBOX`. Each was loaded
in-process and sent only as the `Authorization` header of a bare
`{ viewer { id name } }` POST to `https://api.linear.app/graphql`. Neither key
was printed, logged or put in a command's arguments. Each read costs its own
user one request. The reader prints the viewer's id, so each key's owner was
confirmed at every read:

| key | viewer | id |
| -- | -- | -- |
| `LINEAR_API_KEY_FLEET` | `Agent-Bureau` | `cebc4c53-fad2-410f-be31-f920b6ad773f` |
| `LINEAR_API_KEY_SANDBOX` | `bureau-sandbox` | `e1ce0a23-74c8-4255-b0fd-20a0bf301b32` |

Those are the same two ids `docs/sandbox-seat-proof-2026-09.md` §1 recorded.

### 2.2 The bracket

Before dispatching, `gh run list` showed no `Integration Harness` run in
flight (the last, 37077266270, completed at 16:31:50 PT). The before-read and
the dispatch ran in one command. The after-read ran in the same background
command as `gh run watch 37078484136 --exit-status --interval 3`, so it fired
within seconds of the run's end.

| when (PT) | what | fleet `x-ratelimit-requests-remaining` | sandbox `x-ratelimit-requests-remaining` |
| -- | -- | -- | -- |
| 16:37:43 | identity check (above) | 1218 / 2500 | 2499 / 2500 |
| 16:37:49 | **before** | **1221** / 2500 | **2499** / 2500 |
| 16:37:49 | `gh workflow run harness.yml -R dreadnought-foundry/bureau-pipeline --ref main` | — | — |
| 16:45:59 | the run ends (`updatedAt`) | — | — |
| 16:46:01 | **after** | **1436** / 2500 | **2499** / 2500 |

At both instants the two keys read different numbers: 1,221 against 2,499
before, and 1,436 against 2,499 after.

### 2.3 The run

Run [37078484136](https://github.com/dreadnought-foundry/bureau-pipeline/actions/runs/37078484136):
event `workflow_dispatch`, ref `main`, `pipeline_ref` left at its default
`main`, head `d07a8b0b4e5767c1da890ef26df43f74b85fa8be` (the step's
environment shows `HARNESS_TESTED_SHA: d07a8b0b4e5767c1da890ef26df43f74b85fa8be`),
created 16:37:51 PT, ended 16:45:59 PT, conclusion **success**. It ran the
default sweep: `agent_task_parses`, `bot_pr_flow`, `dependabot_flow`,
`gate_paths`, `lane_contract`. Every step's environment echoes
`LINEAR_IDENTITY: sandbox`. The `Run harness scenarios` step shows
`LINEAR_API_KEY: ***`. The step's `linear-budget:` line, verbatim:

```
linear-budget: 2499 → 2499 (spent 0 this run; window resets 17:45 PT; limit 2500; budget: sandbox)
```

Its log timestamp is 16:45:50 PT. The driver's output reaches the log in one
flush at the end of the step: every line from the step carries 16:45:50 PT.
`linear_ops` prints this line at process exit (`atexit`, to stderr), which is
why it lands among the `dependabot_flow` lines rather than after
`lane_contract`. The `lane_contract` read itself is in the log:
`[lane_contract] Linear carries 11 state(s): …`, then
`15 asserted, 0 failed, 36 skipped (phase not shipped), 1 unevaluated`.

**The harness line's two numbers sit inside the sandbox's pair, not the
fleet's.** It read 2,499 at both ends. The sandbox read 2,499 before and
2,499 after. The fleet read 1,221 to 1,436, and no fleet line in the window
(§2.4) came within 1,000 of 2,499.

**An honest limit on the sandbox pair.** It did not move, though at least
four sandbox requests were made inside it: the two header reads, the
harness's one read, and the sandbox's `Agent Task` run below. The header
does not show a single request a few seconds later: the identity read at
16:37:43 and the before-read at 16:37:49 both returned 2,499. So the pair
cannot count the harness's one request. What it does show is *which bucket* that request came from.

### 2.4 Every run that printed a spend line in the window

Every run in the six repo-map repos and in `bureau-harness` whose life
overlapped 16:37:49 to 16:46:01 PT was listed with `gh run list`. The first
listing read each repo's 100 newest runs. A second listing, by creation date
back to 14:30 PT, found two long runs the first had missed: portico's Agent
Task 37073680791, which printed a fleet line inside the window (in the table),
and bureau-pipeline's Pipeline Tests 37077266163, which printed none. Each log was
read with `gh run view --log` for lines that open with `linear-budget:` or
`sweep-spend: total`. Logs that were not ready at the first pass were read
again at about 16:54 PT. Lines stamped inside the window:

| repo | run | workflow | line(s) inside the window (PT) | bucket |
| -- | -- | -- | -- | -- |
| bureau-pipeline | [37078484136](https://github.com/dreadnought-foundry/bureau-pipeline/actions/runs/37078484136) | Integration Harness | 16:45:50 `2499 → 2499 (spent 0 …; budget: sandbox)` | sandbox |
| bureau-harness | [37078512827](https://github.com/dreadnought-foundry/bureau-harness/actions/runs/37078512827) | Agent Task (the harness's `agent_task_parses` dispatch, canceled by the driver) | 16:38:25 `2499 → 2499 (spent 0 …; budget: undeclared)` | sandbox (`bureau-harness`'s key is the sandbox key, 2026-09 record §1; the 2,499 is the sandbox's number) |
| portico | [37078280794](https://github.com/dreadnought-foundry/portico/actions/runs/37078280794) | QA Review | 16:38:48 `1259 → 1258 (spent 1 …; budget: undeclared)` | fleet |
| portico | [37078558369](https://github.com/dreadnought-foundry/portico/actions/runs/37078558369) | Merge Gate | 16:39:09 `1270 → 1270 (spent 0 …)`; 16:39:15 `1273 → 1272 (spent 1 …; budget: undeclared)` | fleet |
| portico | [37078588810](https://github.com/dreadnought-foundry/portico/actions/runs/37078588810) | Linear Sync | 16:40:03 `1300 → 1299 (spent 1 …; budget: fleet)`; 16:40:04 `1298 → 1298 (spent 0 …)`; `sweep-spend: total 0 request(s)` at 16:40:11 and 16:41:13 | fleet |
| portico | [37078699087](https://github.com/dreadnought-foundry/portico/actions/runs/37078699087) | Reconcile | 16:40:57 `1300 → 1300 (spent 0 …; budget: fleet)` | fleet |
| portico | [37078701192](https://github.com/dreadnought-foundry/portico/actions/runs/37078701192) | Reconcile | 16:41:18 `1309 → 1309 (spent 0 …; budget: fleet)` | fleet |
| portico | [37073680791](https://github.com/dreadnought-foundry/portico/actions/runs/37073680791) | Agent Task (created 15:39:50 PT, ended 16:46:04 PT) | 16:45:57 `1437 → 1436 (spent 1 …; budget: undeclared)` | fleet |

All the other overlapping runs printed no such line: 28 of portico's 34, 3
of bureau-pipeline's 4 (a skipped Merge Gate, a Promote Channel run and the
Pipeline Tests run), and
40 of bureau-harness's 41, including the harness's own probe
PRs' CI, critic and merge-gate runs. Some of those had no log because they
were skipped. Two portico runs were still in progress at the second read
(Release train 37078748720 and CI 37079106902). No run of either workflow
read here printed a budget line. Portico QA Review 37079107324 started at 16:45:59 PT and printed
its first line at 16:46:30 PT, after the window. `atlas`, `deltasolv`,
`agent-bureau` and `agent-bureau-demo` had no run in the window. No
`bureau-harness` reconcile sweep ran in the window.

**The fleet's reading, traced.** It rose by 215, from 1,221 to 1,436. A rise
cannot be traced to runs: the fleet's hour is rolling, and it refilled as
requests from an hour earlier aged out. The in-window fleet lines show the
same climb: 1,259 at 16:38:48, 1,273 at 16:39:15, 1,300 at 16:40:03, 1,309 at
16:41:18, 1,436 at 16:45:57 (the portico Agent Task's line), and 1,436 at
16:46:01. What can be traced is every fleet spend
inside the window:

- **4 requests** on the runs' own budget lines: portico QA Review (1),
  portico Merge Gate (1), portico Linear Sync (1) and portico Agent Task (1).
  Linear Sync's own `sweep-spend: total` lines say 0. `check_linear_budget.py`
  charges a run that printed a sweep total by that total, so by the script's
  rule the window holds 3. This record counts 4, the larger number.
- **2 requests** by this record's own header reads (before and after).
- **0 requests** by the harness. It printed no fleet line, and its one line
  names `sandbox` at 2,499.

Fleet traffic that leaves no Actions log could also have spent in the window
and is not counted here: the console backend, the relay, and any repo outside
`config/repo-map.json` (vericorr and project-template were not read). The
CEO disabled bureau-pipeline's own agent, sweep and groomer workflows at
15:17 PT, but the product repos' stubs still call the reusable workflows. That
is why portico's runs appear above.

**The verdict on the fleet reading.** The card asks for the two fleet
readings to be equal, or for every unit of difference to be traced to a named
fleet run. Neither is literally true. The readings are not equal, and the
difference is a refill, which no run's line can account for. The fleet bucket
did not fall across the run. Every fleet spend line in the window is named
above, and none is the harness's. As in the 2026-09 record's §2.4, a rise
across a rolling window is consistent with "not lowered" but does not prove
"zero fleet requests." The harness's own `budget: sandbox` line, reading a
bucket 1,000 above the fleet's at the same minutes, is what carries that.

### 2.5 `check_linear_budget.py --hours 1`

Run read-only from a scratch clone of `main` at `d07a8b0`, 16:49:55 to
16:53:29 PT. Its window is the hour ending 16:49:55 PT, which is wider than
the bracket; §2.4 is that table filtered to the bracket, run by run.

```
linear budget, last 1h
repo                 workflow                          runs   spent  max/run
----------------------------------------------------------------------------
portico              Reconcile                            7      91       30
bureau-pipeline      Linear Sync                          1      35       35
portico              Linear Sync                          3       7        7
portico              QA Review                            3       7        5
agent-bureau-demo    QA Review                            1       6        6
bureau-pipeline      QA Review                            1       5        5
portico              Merge Gate                           8       2        1
bureau-pipeline      Merge Gate                           3       1        1
agent-bureau-demo    CI                                   1       0        0
agent-bureau-demo    Merge Gate                           1       0        0
agent-bureau-demo    Verify                               1       0        0
bureau-pipeline      Integration Harness [sandbox]        2       0        0
bureau-pipeline      Pipeline Tests                       1       0        0
bureau-pipeline      Promote Channel                      3       0        0
bureau-pipeline      Release Gate                         2       0        0
portico              CI                                   4       0        0
portico              Release train                        2       0        0
portico              Specimen                             6       0        0
----------------------------------------------------------------------------
TOTAL                                                    50     154   (77 run(s) skipped: log not available)
```

The harness has its own row, `Integration Harness [sandbox]`, split off the
fleet's by the bucket its line names (DRE-5589). Its two runs in the hour
(16:22:32 PT push run 37077266270 and this dispatch) spent 0. The script does
not read `bureau-harness`, which is not in `config/repo-map.json`.

## 3. Did the claim hold?

**Yes.** The driver's one read is on the sandbox seat, the first `main` run
proved it, and `stable` kept moving. One criterion of the build card did not
hold, and it is about the pull request's prose, not the mechanism.

### DRE-3651 — the harness driver's read moves to the sandbox seat

| criterion | held? | evidence |
| -- | -- | -- |
| `Run harness scenarios` sets `LINEAR_API_KEY` to exactly `${{ secrets.LINEAR_API_KEY_SANDBOX }}`; no step reads `secrets.LINEAR_API_KEY` as a whole token; the `harness` job's job-level `env` carries `LINEAR_IDENTITY: sandbox` and no step carries it; the tests are committed before the workflow change | **held** | `main` at `d07a8b0`, `.github/workflows/harness.yml`: line 527 `LINEAR_API_KEY: ${{ secrets.LINEAR_API_KEY_SANDBOX }}`; `${{ secrets.LINEAR_API_KEY }}` appears nowhere; line 209 `LINEAR_IDENTITY: sandbox` in the job's `env:` beside `BUREAU_APP_ID_2/3/4`, the file's only `LINEAR_IDENTITY`. PR #636's commits, in order: `3670462ce` `test(DRE-3651)` (`tests/test_harness_wiring.py`, `tests/test_harness_lane_contract.py`), then `155bebbcf` `feat(DRE-3651)` (`harness.yml`, `scripts/harness/README.md`). `TDD commit discipline` passed. Live: run 37078484136's every step echoes `LINEAR_IDENTITY: sandbox` (§2.3) |
| `test_the_workflow_gives_it_a_linear_key` asserts the sandbox secret and no longer the fleet's | **held** | `main` `tests/test_harness_lane_contract.py` lines 66–67: `assert "LINEAR_API_KEY: ${{ secrets.LINEAR_API_KEY_SANDBOX }}" in text` and `assert "${{ secrets.LINEAR_API_KEY }}" not in text` |
| the five named suites are green | **held** at merge (PR CI) and on `main` CI | PR #636 `scripts unit tests` passed (21m46s). `Pipeline Tests` on `main` run [37074456873](https://github.com/dreadnought-foundry/bureau-pipeline/actions/runs/37074456873) (`fc580f8`, 15:48 PT) passed, including `test_linear_identity_declared.py::RateLimitedSweepNamesTheUserTest::test_a_rate_limited_sweep_exits_75_naming_the_user`. One note: a local run of the five files from the scratch clone at `d07a8b0` (Python 3.12 and 3.14) gave 67 passed and 1 failed, that same test. It expects the text `16:32 PT`, and locally the error carried no window clock. It passes in CI, it is not in DRE-3651's diff, and the local cause was not chased |
| `scripts/harness/README.md`'s "The Linear side" says the read is made as `bureau-sandbox` | **held** | `main` line 270: "Since DRE-3651 that read is made as the `bureau-sandbox` seat, not as the fleet: …" |
| the PR body names the first post-merge `Integration Harness` run on `main` as the PROOF card's observation, and claims nothing about it | **did not** | PR #636's body is the push-rescue step's text ("Opened by the run's push-rescue step, not by the agent (DRE-3043) … expect no author-written summary"), never edited (no `userContentEdits`), and no PR comment names the observation. It claims nothing, so the half about not over-claiming holds; the half that names the observation does not |

### The deferral DRE-3636's record carries for this epic

| criterion | held? | evidence |
| -- | -- | -- |
| `docs/sandbox-seat-proof-2026-09.md`, "Deferred to DRE-3650 — the harness driver's one read": "the harness driver (`harness.yml`) reads Linear on the sandbox seat — deferred to DRE-3650, not yet true" | **closed: now true** | §1: run 36948008740, the first `main` run on DRE-3651's merge commit `73bb2be`, ended `budget: sandbox` and concluded success, and `stable` moved past it. §2 repeats it on run 37078484136. That record's third "owed" item (`config/linear-identities.json` says `harness.yml` reads `LINEAR_API_KEY_SANDBOX` while nothing on `main` did) is closed by the same merge |

The CEO reads this record and closes DRE-5451.
