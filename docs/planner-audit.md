# The planner, scored against plans it has never seen

Run live on **2026-09-04** against the real workspace, read-only:

```
python3 scripts/planner_score.py collect --epic DRE-N > history.json
python3 scripts/planner_score.py score  --epic DRE-N --report score.md < history.json
```

Nothing was moved and nothing was written. `collect` and `score` read Linear
and GitHub; the only writer in this module is the replay harness's one
`oneoff` call, and that files into `repo:agent-bureau-demo` or it refuses.

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
| a proof card and a demo card exist | **excluded — see below** |

## The `proof-and-demo` exclusion, and why it stays

`plan.yml` runs `scripts/proof_and_demo.py` and bounces the epic back to
Planning until a `PROOF:` card and a `DEMO:` card exist, blocked by every other
child and neither one FLEET. **The planner cannot leave the workflow without
the answer.** Scoring it grades the gate, not the planner — DRE-2685's
`hand-built` exclusion, one role over. The rows are printed and not counted.

The exclusion is enforced in code and it **checks itself**:
`config/planner-audit.json` names the gate in `enforced_by`, and
`reference_problems()` reads `.github/workflows/plan.yml` for it. Take the gate
out of the workflow and `planner_score.py check` fails until the dimension is
scored again — an exclusion nobody re-checks is how a real result stays out of
the number forever.

On these three epics the exclusion costs the audit **three `missing` rows**,
because all three predate DRE-2746. That is the exclusion doing its job in the
other direction: those rows measure when the convention shipped, not whether
the planner followed it.

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

## The replay harness

The second half of DRE-3016. `planner-replay.yml` (dispatched from
`self-planner-replay.yml`, on demand and never on a schedule) freezes an
already-planned epic at its **pre-plan text**, files it as a throwaway
`PROOF-PL-<n>` epic labelled `repo:agent-bureau-demo`, and lets the normal plan
rail plan it. The historical plan and the history above are the held-out answer.

Three rules bound it, all in code rather than in this document:

* **It files cards in `agent-bureau-demo` and nowhere else.**
  `planner_score.replay_problems()` refuses the card before it is created if the
  repo label names anything else or the title is not a throwaway, and the
  workflow holds `contents: read` and checks out no product repo.
* **A replay that was shown the answer is discarded.** `plan_leaks()` looks for
  distinctive lines of the historical plan in whatever the replay was handed;
  finding any raises `LeakedPlan`, and `leak_record()` writes the leak down.
  Boilerplate every plan shares — `## The cards` — is deliberately not a leak,
  because a check that fires on every replay is a check nobody leaves on.
* **The frozen text is the epic as the CEO wrote it.** `pre_plan_text()` strips
  the `mid_epic` growth record, which is planner output spliced into the epic's
  own description.

The leak check runs **before** the card is filed, not after. A replay that saw
the answer cannot be salvaged by scoring it more carefully, and discarding one
after the fact has already spent a planner run. Its reference is
`historical_plan()` — the cards the plan actually cut, because the
decomposition *is* the plan.

### What the diff compares, and what it deliberately does not

`planner_score.py diff --before <historical> --after <replay>` puts the two
decompositions side by side:

| | DRE-2514 | DRE-2668 | how to read it |
| --- | --- | --- | --- |
| `cards` | 47 | 50 | how many cards the epic was cut into |
| `with-footprint` | 0 | 0 | cards declaring a `**Files:**` line |
| `footprint-collisions` | 0 | 0 | pairs whose DECLARED footprints intersect |
| `serialized-pairs` | 11 | 41 | pairs wired with a real `blockedBy` relation |
| `with-verdict` | 0 | 13 | cards carrying a routing verdict |

*(Two historical epics, run as a smoke test of the command — DRE-2668 is not a
replay of DRE-2514.)*

**It is not a per-card comparison, and it does not pretend to be.** Nothing can
mechanically say which replay card corresponds to which historical child;
matching on the declared footprint would be the obvious way and 0 of 147 cards
declare one. So what is diffed is the SHAPE of the decomposition, and a moved
number is a question to go and look at rather than a verdict on a card. A
replay's own children never merge — nothing ships from the demo repo — so
scoring the replay reports UNKNOWN on every history row, correctly, which is
why the shape is the half that moves.

No replay has been run yet. The harness ships with this card; the first replay
belongs to the model comparison DRE-3016 links to, which needs the new ladder to
exist.

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
