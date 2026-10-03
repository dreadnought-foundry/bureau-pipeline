# Sweep spend proof — DRE-3646 (epic DRE-3623)

**Status: PARTIAL. Two of the five observations pass, one was not observed, and two fail.** Read on
2026-09-28 between 19:55 and 20:20 PT from `gh run view <id> --log` against the
live board, by an operator session. Every time below is Pacific Time.

| Criterion | Result |
|---|---|
| Three consecutive `schedule` sweeps on bureau-pipeline at or under 30 | **Pass** — 20, 20, 20 |
| One card-done dispatch scoped to one card, at or under 15 | **Pass** — 6 (DRE-3644's own Done) |
| The run summary page shows `linear-budget:` and `sweep-spend:` | **Not observed** — see §3 |
| `check_linear_budget.py --hours 1`: every bureau-pipeline reconcile row `max/run` ≤ 30 | **Fails as written** — 59, and the number measures the wrong thing (§4). DRE-5201 reads it per run (§6) |
| agent-bureau: three consecutive sweeps at or under 30 after promotion | **Fails** — 39, 30, 36 |

## 0. Which code the sweeps ran

- DRE-3644 merged into bureau-pipeline as `2d3fa4c88d86c7db15b70fdb70496674a41f7904`
  (PR #551) at 19:10 PT.
- **bureau-pipeline rides `@main`, not `stable`.** Its `self-reconcile.yml` calls
  `reconcile.yml@main` with `pipeline_ref: main`. Each run's log states the sha it
  resolved (`Uses: …reconcile.yml@refs/heads/main (<sha>)`). Every run below from
  19:11 PT on resolved to `44f2471ed2` or `26c888d5f4`, and both contain `2d3fa4c88d`.
- **agent-bureau rides `@stable`.** At 20:00 PT the `stable` tag pointed at
  `26c888d5f4cb1fe9f1c35a8f2a4dd4470d60a672`. GitHub's compare shows it 9 commits
  ahead of `2d3fa4c88d` and 0 behind, so it contains DRE-3644. Promote Channel ran on
  `2d3fa4c88d` at 19:10 PT and on `26c888d5f4` at 19:12 PT. Every agent-bureau pass
  counted below resolved `stable` to `26c888d5f4` (per its `Uses:` line).
- The card body says agent-bureau "rides a release tag" and waits for the operator
  to promote a release. That is out of date. Since DRE-2553 the stub rides `@stable`,
  which advances automatically. agent-bureau's stub also passes `sweep_reason` and
  `sweep_card`.

## 1. Three consecutive scheduled sweeps on bureau-pipeline

All three are full passes (`sweep-scope: full pass (schedule)`) on main `26c888d5f4`.

| Run | Started (PT) | refresh_stale_merge_refs | flag_unlanded_work | close_finished_epics | promote_ready | report_break_glass | report_rereview_missing | **total** |
|---|---|---|---|---|---|---|---|---|
| [36513282143](https://github.com/dreadnought-foundry/bureau-pipeline/actions/runs/36513282143) | 19:35 | 1 | 7 | 2 | 5 | 1 | 4 | **20** over 6 phases |
| [36514705282](https://github.com/dreadnought-foundry/bureau-pipeline/actions/runs/36514705282) | 19:53 | 1 | 7 | 2 | 5 | 1 | 4 | **20** over 6 phases |
| [36515621416](https://github.com/dreadnought-foundry/bureau-pipeline/actions/runs/36515621416) | 20:05 | 1 | 7 | 2 | 5 | 1 | 4 | **20** over 6 phases |

The last scheduled pass before the merge was
[36510576205](https://github.com/dreadnought-foundry/bureau-pipeline/actions/runs/36510576205),
started 19:00 PT on `c44bbd4101`. It spent **64** over 8 phases, including
`report_epic_growth 24`, `promote_ready 16` and `flag_stranded 7`. In all three
post-merge passes `report_epic_growth` does not appear at all. That is the
change DRE-3644 made: the growth report reads the pass's batched epic record.

One more full pass fired by `epic-activated`,
[36516127069](https://github.com/dreadnought-foundry/bureau-pipeline/actions/runs/36516127069)
at 20:12 PT, spent **22** over 7 phases, with `report_epic_growth 2`.

## 2. A card-done dispatch scoped to one card

[36511389207](https://github.com/dreadnought-foundry/bureau-pipeline/actions/runs/36511389207),
`repository_dispatch`, started 19:11 PT, fired by **DRE-3644** going Done, on
main `44f2471ed2`, which contains the merge:

```
sweep-scope: card-done DRE-3644 — scoped to its dependents and parent (1 pass(es))
sweep-spend: promote_ready 3 request(s)
sweep-spend: total 6 request(s) over 1 phase(s)
linear-budget: 289 → 283 (spent 6 this run; window resets 20:11 PT; budget: fleet)
```

A second example:
[36511533685](https://github.com/dreadnought-foundry/bureau-pipeline/actions/runs/36511533685)
at 19:12 PT for DRE-4211 also spent **6**. On 2026-09-20 the baseline was 0
because the dispatch did not scope at all.

## 3. The run summary page — not observed

GitHub has no API for job summaries, and the browser session could not load the
run page tonight, so **nobody has looked at the page itself**. What the log does
show is the step that writes it. In run 36514705282, the sweep step greps
`^linear-budget:` and `^sweep-spend:` out of `sweep.log`, wraps them under
`### Reconcile sweep — spend`, and appends them to `$GITHUB_STEP_SUMMARY`.
The fallback text is "no linear-budget: line — the pass exited before its
trailer…". That is evidence the summary is written, not that it renders. To
finish this item, open
<https://github.com/dreadnought-foundry/bureau-pipeline/actions/runs/36515621416>
and confirm the "Reconcile sweep — spend" block shows the eight lines in §1.

## 4. The busy-hour table — fails as written, and why

`python3 scripts/check_linear_budget.py --hours 1` was run from bureau-pipeline's
`main` copy, read-only, twice:

Run at 19:56 PT (the hour 18:56–19:56 PT):

```
linear budget, last 1h
repo                 workflow                          runs   spent  max/run
----------------------------------------------------------------------------
agent-bureau         Reconcile                            5     219      106  (15 line(s) unknown/rolled)
portico              Reconcile                            4     179      128  (12 line(s) unknown/rolled)
agent-bureau         Agent Plan                           2     176      105  (1 line(s) unknown/rolled)
bureau-pipeline      Reconcile                            4     118       59  (12 line(s) unknown/rolled)
portico              Agent Plan                           5     107       41  (2 line(s) unknown/rolled)
bureau-pipeline      Agent Plan                           1      62       40
bureau-pipeline      Linear Sync                          2      34       18
agent-bureau         Agent Task                           2      29        5
agent-bureau         Linear Sync                          3      27       15
agent-bureau-demo    Reconcile                            3      23       14  (9 line(s) unknown/rolled)
agent-bureau         Merge Gate                          13       8        6
portico              QA Review                            3       8        5
agent-bureau         QA Review                            1       1        1
agent-bureau         Verify                               3       1        1
bureau-pipeline      Merge Gate                           1       1        1
agent-bureau         Console Release                      2       0        0
agent-bureau         Release train                        2       0        0
bureau-pipeline      Integration Harness                  1       0        0
bureau-pipeline      Nightly Watch                        1       0        0
bureau-pipeline      Pipeline Tests                       2       0        0
bureau-pipeline      Promote Channel                      4       0        0
bureau-pipeline      Release Gate                         2       0        0
portico              Merge Gate                           7       0        0
portico              Specimen                             4       0        0
----------------------------------------------------------------------------
TOTAL                                                    77     993   (200 run(s) skipped: log not available)
```

Run at 20:14 PT (the hour 19:14–20:14 PT, after the merge only):

```
linear budget, last 1h
repo                 workflow                          runs   spent  max/run
----------------------------------------------------------------------------
agent-bureau         Reconcile                            3     201      106  (10 line(s) unknown/rolled)
agent-bureau         Agent Plan                           2     177      105
bureau-pipeline      Reconcile                            4     170       59  (12 line(s) unknown/rolled)
portico              Agent Plan                           6     117       41  (3 line(s) unknown/rolled)
bureau-pipeline      Agent Plan                           1      62       40
portico              Reconcile                            3      51       41  (9 line(s) unknown/rolled)
agent-bureau-demo    Reconcile                            3       9        9  (10 line(s) unknown/rolled)
portico              Agent Fix                            1       9        4  (1 line(s) unknown/rolled)
portico              QA Review                            1       6        5
agent-bureau         Agent Fix                            1       1        1
agent-bureau         QA Review                            1       1        1
agent-bureau         Verify                               4       1        1
portico              Pipeline Medic                       1       1        1
agent-bureau         Merge Gate                           7       0        0
bureau-pipeline      Nightly Watch                        1       0        0
bureau-pipeline      Promote Channel                      1       0        0
bureau-pipeline      Release Gate                         1       0        0
portico              Merge Gate                           8       0        0
portico              Specimen                             2       0        0
----------------------------------------------------------------------------
TOTAL                                                    51     806   (144 run(s) skipped: log not available)
```

**The bureau-pipeline Reconcile row reads `max/run 59`, so the criterion fails
as written.** The four runs in the second window are the three passes in §1
plus the 20:12 PT pass. Their `linear-budget:` lines say 59, 47, 21 and 43
spent, while their own `sweep-spend: total` lines say 20, 20, 20 and 22.

The cause: `linear-budget:` is the *fleet key's* remaining quota read at the
start and end of the run (`budget: fleet`). Every run in every repo on that key
spends from the same counter. So the difference counts whatever else ran in the
same seconds. The clearest case is the 19:35 PT pass. It spent 20 by its own
count, and its budget line starts at 731 remaining. agent-bureau's reconcile
started 12 seconds earlier. Its budget line starts at 732, and it spent 39 by its
own count. The pipeline's budget line reads **59, which equals 20 + 39**. That is
consistent with the two passes sharing one counter, not proof that nothing else
contributed. The per-run number the card's
target is about is `sweep-spend: total`. The budget line and this checker's
`max/run` cannot measure it while two sweeps overlap.

Two more defects in this reading:

- **Every run adds 3 false "unknown/rolled" lines.** The checker matches
  `linear-budget:` anywhere in the log. The summary step's own source code is
  echoed into the log and contains that string three times: the comment, the
  `grep '^linear-budget:'`, and the fallback `echo "no linear-budget: line …"`.
  Each one is counted as a line whose spend "cannot be known". That is why
  bureau-pipeline shows 12 for 4 runs.
- **144–200 runs were skipped with "log not available"** in each window, so the
  totals are floors.

## 5. agent-bureau — promoted, and over thirty

`stable` carried DRE-3644 from 19:12 PT. The last three agent-bureau scheduled
passes, all on `stable` = `26c888d5f4`:

| Run | Started (PT) | Phases | **total** |
|---|---|---|---|
| [36513267027](https://github.com/dreadnought-foundry/agent-bureau/actions/runs/36513267027) | 19:34 | refresh_stale_merge_refs 6 · flag_unlanded_work 10 · recover_limit_deaths 2 · close_finished_epics 2 · promote_ready 15 · report_break_glass 1 · report_rereview_missing 3 | **39** |
| [36514689832](https://github.com/dreadnought-foundry/agent-bureau/actions/runs/36514689832) | 19:53 | refresh_stale_merge_refs 7 · flag_unlanded_work 10 · close_finished_epics 2 · promote_ready 7 · report_break_glass 1 · report_rereview_missing 3 | **30** |
| [36515602713](https://github.com/dreadnought-foundry/agent-bureau/actions/runs/36515602713) | 20:05 | refresh_stale_merge_refs 8 · flag_unlanded_work 10 · flag_stranded 5 · close_finished_epics 2 · promote_ready 7 · report_break_glass 1 · report_rereview_missing 3 | **36** |

The last pass before promotion,
[36510550747](https://github.com/dreadnought-foundry/agent-bureau/actions/runs/36510550747)
at 19:00 PT on `c44bbd4101`, spent **74**, including `report_epic_growth 28`.

`report_epic_growth` is gone from agent-bureau's passes too. The pass still
exceeds 30 on two of three runs. agent-bureau's board is larger than
bureau-pipeline's: `flag_unlanded_work` is 10 against 7, `refresh_stale_merge_refs`
is 6–8 against 1, `promote_ready` swings with the board (7 to 15), and
`flag_stranded` and `recover_limit_deaths` appear on some passes. None of those
phases is one the sibling cards cut. **agent-bureau does not meet 30.**

agent-bureau's card-done dispatches scope as well. For example,
[36511337560](https://github.com/dreadnought-foundry/agent-bureau/actions/runs/36511337560)
at 19:10 PT (`card-done DRE-5057`) spent **5**. That run was still on the older
`stable` = `c44bbd4101`.

## What is left

1. Someone looks at one bureau-pipeline run summary page (§3) and adds the run id here.
2. A decision on the busy-hour criterion: re-express it against `sweep-spend: total`,
   or make the budget line per-run. As written it cannot pass while sweeps overlap (§4).
   **Taken by DRE-5201** — see §6.
3. A decision on agent-bureau: 30 is not met at its board size (39, 30, 36).
   **DRE-5201 proposes a target** — see §6. It is the CEO's to set.
4. The CEO closes the card after reading this record.

## 6. After DRE-5201

**The checker reads a sweep's own count.** `check_linear_budget.py` now charges a
run that printed a `sweep-spend: total` line that number, and does not add its
`linear-budget:` lines, which count the shared key. Every other workflow is still
read off its budget lines. Both markers are read only where they open a line, so
the Sweep step's echoed source no longer adds three "unknown/rolled" lines per
run. Read that way, the 19:35 PT pair in §4 is bureau-pipeline 20 and
agent-bureau 39, a total of 59 — not 59 and 60. `max/run` is also now the most
one run spent, all of its processes together, rather than the busiest single line.

**What agent-bureau's pass costs, phase by phase** (the three passes in §5,
read from the code that spends them):

| Phase | Cost | What drives it |
|---|---|---|
| `flag_unlanded_work` | 10, 10, 10 | It is the first phase to ask for the board, so it pays for the pass's one board read (DRE-2929): the pages over the swept lanes, plus a whole-thread read for each card whose comments overflow the window. It grows with the board. bureau-pipeline's is 7. |
| `promote_ready` | 15, 7, 7 | The dependency gate over the Backlog candidates. It grows with Backlog. |
| `refresh_stale_merge_refs` | 6, 7, 8 | One card read (state and labels) per open, non-draft, non-conflicted card pull request, for the human-park check. It grows with open PRs. bureau-pipeline's is 1. |
| `report_rereview_missing` | 3, 3, 3 | Fixed. |
| `close_finished_epics` | 2, 2, 2 | Fixed. |
| `report_break_glass` | 1, 1, 1 | Fixed. |
| `flag_stranded` | 0, 0, 5 | Only when a card has stranded. |
| `recover_limit_deaths` | 2, 0, 0 | Only when a run died on a limit. |

The phases every pass runs came to 37, 30 and 31 on the three passes, before
any event-driven phase — and 29 with each at its lowest. So at agent-bureau's
current board, 29 is its floor, and 30 is barely above it. The one cut visible from here
is `refresh_stale_merge_refs`'s card read, which the pass's board snapshot could
serve for the cards it already holds. That is worth up to 6–8 a pass. Even with
it, the 19:34 PT pass would have spent 33.

**Proposed, not applied:** a target of **40** Linear requests per scheduled pass
for agent-bureau. It covers 30, 36 and 39 on the current code, and it is about
54% of the 74 the last pass before DRE-3644 spent. bureau-pipeline keeps 30.
This run could not read agent-bureau's runs (its token is scoped to
bureau-pipeline), so the figures above are the ones §5 recorded on 2026-09-28.
