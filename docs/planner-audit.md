# The planner, scored against plans it has never seen

Run live on **2026-09-04** against the real workspace, read-only:

```
python3 scripts/planner_score.py collect --epic DRE-N > history.json
python3 scripts/planner_score.py score  --epic DRE-N --report score.md < history.json
```

Nothing was moved and nothing was written. `collect` and `score` read Linear
and GitHub, and nothing in this module writes to either.

## What the planner is scored against

The critic's audit (DRE-2685) compares its verdicts to a human review it was
never shown. The planner's held-out answer needs no human at all: **every claim
a plan makes has a mechanical answer in what its children then did**, and none
of it existed when the plan was written.

| what the plan claimed | what history says |
| -- | -- |
| the `**Files:**` footprint each card would touch | the files its merged PR actually touched |
| which cards collide, as `blockedBy` edges | which pairs' merged PRs really shared a file |
| the card is one PR's worth | whether the run died at the turn cap |
| the card was build-ready at creation | the readiness guard's own return receipt |
| the routing verdict | an escalation or hand-back — a FLEET card that needed a person |
| the plan was approved as written | the plan critic's send-backs, the mid-epic amendment markers |
| the card survives as one card | the split ledger's own population — a turn-cap death, a cancel with pieces citing it, a hand-back |
| a proof card exists | **excluded — see below** |

## The split rate (DRE-3079)

DRE-3022 asked to be measured by one number, and this is the reader that
answers it:

```
python3 scripts/planner_score.py split-rate --month 2026-09
```

**How often did a planner-created child have to be split, month by month?** A
child counts as split when the pipeline's own record says one run of it was not
enough. That population is `config/split-ledger.json`'s, read through
`split_ledger.reasons` — not a second definition, so the ledger and the rate
can never disagree about what "did not fit one run" means. It is WIDER than the
`size` row above, which reads the turn-cap receipt and nothing else: a card
handed back to Planning as an epic never hit the cap and agrees on `size`,
while being the clearest split there is.

`split-rate` and `score` both take `--ledger <path>` to read another ledger
instead; without it they read the shipped file. The tests pass their own,
because the shipped file is regenerated every night (DRE-5314).

Four answers per card, and the last two are the load-bearing ones: `split`,
`one-card`, `pending` (the card has not finished, so the question was never put
to it) and `unknown` (its record could not be read). Only the first two are in
the denominator. Counting `pending` as `one-card` would make the rate improve
every time the board grows.

**The boundary is set, and the after half is not a clean month yet.** The ledger
reached the planner in **DRE-3359 (PR [#359](https://github.com/dreadnought-foundry/bureau-pipeline/pull/359))**,
merged `2026-09-10T22:16:25Z` — 2026-09-10 15:16 PT — and that is the date
`ledger_injected_at` in `config/planner-audit.json` now holds (set by DRE-3363).
The command draws the comparison from it by MONTH: every month earlier than
2026-09 reports *before*, and 2026-09 itself and everything after it report
*after*. That is what it cannot report cleanly yet — September straddles the
boundary and the shipped reader has two sides, not three, so September's *after*
figure includes cards cut before the planner had ever seen the ledger, and it is
not over either. The first month that answers the question cleanly is October,
read after 1 November. A bucket with no finished
cards still reports **UNKNOWN**, never an empty bucket printed as zero — which
would read as an improvement the pipeline has not made.
`docs/split-ledger-audit.md` records the reading the date was set from.

## The `proof-and-demo` exclusion, and why it stays

`plan.yml` runs `scripts/proof_and_demo.py` and bounces the epic back to
Planning until a `PROOF:` card exists — last, blocked by every other child and
not FLEET. **The planner cannot leave the workflow without the answer.**
Scoring it grades the gate, not the planner — DRE-2685's
`hand-built` exclusion, one role over. The rows are printed and not counted.

The exclusion is enforced in code and it **checks itself**:
`config/planner-audit.json` names the gate in `enforced_by`, and
`reference_problems()` reads `.github/workflows/plan.yml` for it. Take the gate
out of the workflow and `planner_score.py check` fails until the dimension is
scored again — an exclusion nobody re-checks is how a real result stays out of
the number forever.

On these three epics the exclusion costs the audit **three `missing` rows**: all
three predate DRE-2746, and none carries the closing card. Re-checked live on
2026-09-04 across every child of each epic — DRE-2514 (47) and DRE-2668 (116)
carry no closing card at all; DRE-2628 (68) carries a demo card
(DRE-2638) and no `PROOF:` card, so the proof is still absent and the row is
still `missing`. DRE-3669 halved the shape to one closing child on 2026-09-12
and left all three rows where they were: what is missing on each of them is the
proof. That is the exclusion doing its job in the other direction: those
rows measure when the convention shipped, not whether the planner followed it.

## The epics

The three with the most children in Done, counted over all 164 epics on the
board on 2026-09-04:

| Epic | Repo | Children | In Done |
| -- | -- | -- | -- |
| **DRE-2514** — [WAVE 1] Build the safety rail | bureau-pipeline | 47 | 47 |
| **DRE-2668** — [WAVE 1.5] The intake gate | agent-bureau | 50 | 33 |
| **DRE-2628** — [EPIC] Forms | portico | 50 | 32 |

## Agreement

| Epic | agree | disagree | could not be read | never claimed | excluded |
| -- | -- | -- | -- | -- | -- |
| DRE-2514 | 25 | 23 | 81 | 94 | 1 |
| DRE-2668 | 37 | 65 | 109 | 87 | 1 |
| DRE-2628 | 0 | 3 | 101 | 100 | 1 |

Where the planner agrees with history it agrees on the cheap dimensions, and
they should be read as such:

* **`size` — 23 agreements.** A card that merged as one PR without hitting the
  turn cap. This says nothing went wrong; it does not say the planner sized it.
* **`readiness` — 26 agreements.** A card the readiness guard never returned.
* **`routing` — 3 agreements**, all on DRE-2668 and all FLEET cards that
  shipped unattended.
* **`approval` — 2 agreements.** Neither DRE-2514 nor DRE-2668 carries a
  plan-critic send-back or a mid-epic amendment: the plan the CEO approved is
  the plan that ran.
* **`collision` — 8 agreements.** Pairs the plan serialized that really did
  share a file when they merged.

## Disagreement

Three findings, and the first two are the reason this instrument exists.

### 1. The declared footprint does not exist. 147 of 147 cards.

**Not one card in any of the three epics carries a `**Files:**` line.** Every
`file-footprint` row across all three epics came out `unclaimed` — the plan made
no claim at all.

`briefs/planner.md` is explicit that the line "is not documentation added at the
end; it is the INPUT to the ordering, and you cannot cut a parallel-safe plan
without it." `standards/engineering.md` requires the same thing under *Don't
fight over shared files*, and `standards/card-quality.md` tell 6 says to cut on
file footprint rather than only on concern. Three documents require it, the
planner brief carries a contention pre-flight for it, and **the population where
it should be easiest to find has none**.

This is not a scoring artifact. It is the exact defect DRE-2837/2838 were
written up for: "the rule existed and **was not applied**." The audit's answer
is that it still is not.

### 2. 80 sibling pairs shared a file with nothing serializing them.

| Epic | pairs the plan left parallel that shared a file | most-shared file |
| -- | -- | -- |
| DRE-2514 | 23 | `README.md` (13 pairs) |
| DRE-2668 | 57 | `.github/workflows/plan.yml` (26 pairs) |

`.github/workflows/plan.yml` is the case the brief names and the one that
costs: 26 pairs of DRE-2668's children edited the same 1,458-line workflow file
with no `blockedBy` edge between them. It is not a barrel, it cannot be made
append-only, and nothing carved a foundation card that owns it.

**Read the `README.md` number with more care.** A README is close to
append-only in practice, so many of those 13 pairs would have merged cleanly.
The dimension reports a shared file, not a conflict that happened, and it
cannot tell the two apart — a limit worth writing down rather than a number to
quote. `.github/workflows/plan.yml` is not close to append-only, and neither is
`config/lane-contract.json` (8 pairs).

### 3. Eleven cards died at the turn cap.

| Epic | cards |
| -- | -- |
| DRE-2668 | DRE-2826, DRE-2838, DRE-2845, DRE-2846, DRE-2847, DRE-2852, DRE-2871, DRE-2891 |
| DRE-2628 | DRE-2917, DRE-3012, DRE-3037 |

The audit found this from the cards' own `turn-exhaustion-requeue` receipts,
knowing nothing about any of them. Three of the eight on DRE-2668 —
**DRE-2838, DRE-2847 and DRE-2871** — are the three cards
`standards/card-quality.md` was rewritten around, by name, for being too big.
The instrument rediscovered the population the standard was written about
without being told it existed, which is the closest thing to a calibration
check this audit can have.

## Could not be read

**291 rows, and every one of them is reported as UNKNOWN rather than as
agreement.** Two causes, both named on the row:

1. **A card with no merged pull request.** Nothing was put to the test, so
   nothing is scored.
2. **A repo this run's token cannot see.** DRE-2668 lives in `agent-bureau` and
   DRE-2628 in `portico`; the audit run had a token scoped to
   `bureau-pipeline`.

Cause 2 was very nearly a silent zero, and it is worth the paragraph.
`gh pr list --repo <invisible> --search head:agent/DRE-N` **exits 0 and prints
`[]`.** Nothing fails, so `card_pr`'s rc-!=-0 guard (DRE-2034) never fires, and
an unreadable repo is indistinguishable from a card that never produced a pull
request. Left alone, this audit would have reported every child of a cross-repo
epic as `within-footprint` against a file list nobody ever read — a clean sheet
composed entirely of reads that did not happen. `collect` now probes each repo
once with `gh repo view` and marks the whole repo unreadable, and
`tests/test_planner_score.py` pins both directions of it.

DRE-2628's row is the honest consequence: **0 agreements**, because almost
nothing about it could be read from here. That is the correct output, and it is
the difference between this audit and one that would have scored it 100%.

## Never claimed

**281 rows.** 147 are the missing `**Files:**` lines above. The other 134 are
cards carrying **no routing verdict** — all 47 of DRE-2514, all 50 of DRE-2628,
and 37 of DRE-2668.

That is not the planner ignoring a convention. Routing verdicts arrived with
DRE-2724, after DRE-2514 was planned, and they are **not retroactive**. The
audit reports it as a claim nobody made rather than as a wrong claim, which is
the distinction that keeps the number meaningful. The DRE-2668 split — 13 cards
with a verdict, 37 without — is the convention's rollout, visible in the data.

## What this run says to do next

1. **Make the `**Files:**` line checkable.** The contention pre-flight is
   required by three documents, carried by the planner's brief, and honoured by
   zero of 147 cards. This audit can only ever report `unclaimed` until
   something in `plan.yml` reads the line the way `proof_and_demo.py` reads the
   pair — and once it does, that dimension becomes contaminated too and must be
   marked so in `config/planner-audit.json`. That is the correct trade: a gate
   that produces the behaviour is worth more than a dimension that measures it.
2. **A cross-repo audit needs a cross-repo token.** Two thirds of this run is
   UNKNOWN for no reason other than credentials. The numbers above are a floor,
   not a score.
3. **The `collision` dimension over-reports append-only files.** It reports a
   shared file, not a merge that went DIRTY. Narrowing it to files that cannot
   be made append-only — or corroborating against the merge-gate's own conflict
   receipts — is the next thing that makes the count quotable.

## Reproducing this

```
python3 scripts/planner_score.py check                    # the reference
python3 scripts/planner_score.py collect --epic DRE-2514 > history.json
python3 scripts/planner_score.py score --epic DRE-2514 --report score.md \
  < history.json
```

Linear reads are serial through the one `LINEAR_API_KEY` — no fan-out, the same
bound the critic's audit takes. Each of the three epics above took under a
minute.
