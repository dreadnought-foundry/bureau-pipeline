# The split ledger

<!-- GENERATED FILE — do not edit by hand. -->
<!-- Regenerate with `python3 scripts/split_ledger.py derive`. -->

Generated **2026-09-04T05:21:08Z** from Linear card bodies, labels and comment receipts, plus the merged pull requests of each card's split pieces.

Every card here did not fit one run: it died at the turn cap, it was split, or a build run handed it back as an epic. The point of writing it down is DRE-3022's: the planner has been sizing cards against nothing.

**A read that failed says `UNKNOWN`, never 0 and never "none".** "GitHub would not say" and "the pull request touched nothing" are different facts, and a ledger that collapses them reports a history that never happened.

## The rates

10 card(s) in the ledger, 9 of which died at the turn cap at least once. They cost **$515.44** in dead runs.

6 card(s) declared no footprint at all. They are counted apart from every band below, never into one — an unread card in a denominator is a rate nobody can check.

| Declared footprint | Cards | Died | Rate |
| --- | --- | --- | --- |
| more than 1 file | 3 | 2 | 67% |
| more than 2 files | 3 | 2 | 67% |
| more than 3 files | 3 | 2 | 67% |
| more than 4 files | 3 | 2 | 67% |
| more than 5 files | 3 | 2 | 67% |
| more than 6 files | 1 | 0 | 0% |

- cards declaring more than 1 file died 2 of 3 times
- cards declaring more than 2 files died 2 of 3 times
- cards declaring more than 3 files died 2 of 3 times
- cards declaring more than 4 files died 2 of 3 times
- cards declaring more than 5 files died 2 of 3 times
- cards declaring more than 6 files died 0 of 1 times

## The tells, in hindsight

DRE-2893's four tells, read back over each card's own body by `split_ledger.tells` — a deterministic reading of the text, not a judgement. Each one under-reports on purpose.

| Tell | What it asks | Cards | Died |
| --- | --- | --- | --- |
| `contracts-between-pieces` | Does one deliverable read what another writes? The strongest tell — if B reads what A writes it is not one card. | 2 | 2 |
| `two-languages-or-tiers` | Does the declared footprint span two languages or two tiers? Bounded is not the same as small. | 7 | 6 |
| `unenumerated-count` | Does a criterion count something the body never enumerates? DRE-2837 said "the nine derivations" and the nine were named nowhere. | 2 | 1 |
| `unbounded-quantifier` | Does the card quantify without a bound — "every surface", "all call sites"? DRE-2838's was 57 mount sites. | 2 | 2 |

## The rows

| Card | Size | Role | Declared | Pieces touched | Pieces | Deaths | Cost | Tells | Why it is here |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| [DRE-2676](https://linear.app/dreadnoughtfoundry/issue/DRE-2676/stop-reading-dependencies-out-of-prose-entirely-a-blocker-is-a-linear) | M | engineer | `UNKNOWN` | `UNKNOWN` | 0 | 3 | $64.38 | — | `turn-cap-death` |
| [DRE-2719](https://linear.app/dreadnoughtfoundry/issue/DRE-2719/everything-goes-to-planning-which-decides-one-off-epic-or-wave) | L | devops | `UNKNOWN` | 30 files | 6 | 2 | $42.03 | — | `turn-cap-death`, `split`, `handed-back` |
| [DRE-2838](https://linear.app/dreadnoughtfoundry/issue/DRE-2838/every-rendered-claim-carries-its-age-and-the-storedlive-split-dies) | M | engineer | `UNKNOWN` | `UNKNOWN` | 3 | 4 | $81.18 | `two-languages-or-tiers`, `unbounded-quantifier` | `turn-cap-death`, `split` |
| [DRE-2847](https://linear.app/dreadnoughtfoundry/issue/DRE-2847/close-the-back-doors-into-backlog-and-todo-and-prove-the-absence-by) | M | engineer | `UNKNOWN` | 20 files | 3 | 2 | $47.23 | `unenumerated-count`, `unbounded-quantifier` | `turn-cap-death`, `split` |
| [DRE-2871](https://linear.app/dreadnoughtfoundry/issue/DRE-2871/eight-surfaces-stop-asserting-what-they-never-read-and-unknown) | M | engineer | `UNKNOWN` | `UNKNOWN` | 3 | 6 | $105.94 | `contracts-between-pieces`, `two-languages-or-tiers` | `turn-cap-death`, `split` |
| [DRE-2891](https://linear.app/dreadnoughtfoundry/issue/DRE-2891/four-cutover-surfaces-carry-their-read-age-or-say-unknown) | M | engineer | `UNKNOWN` | `UNKNOWN` | 0 | 3 | $46.02 | `two-languages-or-tiers` | `turn-cap-death` |
| [DRE-2937](https://linear.app/dreadnoughtfoundry/issue/DRE-2937/a-cold-cache-renders-nothing-needs-you-into-the-bell-and-fixing-it) | L | engineer | 1 file | `UNKNOWN` | 2 | 4 | $66.87 | `two-languages-or-tiers` | `turn-cap-death`, `split` |
| [DRE-3016](https://linear.app/dreadnoughtfoundry/issue/DRE-3016/score-the-planner-against-plans-it-has-never-seen-replay-past-epics) | L | engineer | 6 files | `UNKNOWN` | 0 | 1 | $22.57 | `two-languages-or-tiers` | `turn-cap-death` |
| [DRE-3022](https://linear.app/dreadnoughtfoundry/issue/DRE-3022/the-planner-sizes-against-the-ledger-every-turn-cap-death-split-and) | M | engineer | 6 files | `UNKNOWN` | 3 | 2 | $39.22 | `contracts-between-pieces`, `two-languages-or-tiers` | `turn-cap-death`, `split` |
| [DRE-3029](https://linear.app/dreadnoughtfoundry/issue/DRE-3029/planning-classifies-the-card-itself-the-planner-run-stamps-one-off) | M | engineer | 7 files | `UNKNOWN` | 0 | 0 | $0.00 | `two-languages-or-tiers`, `unenumerated-count` | `named-as-a-seed` |

## The footprints

What each card SAID it would touch, against what its pieces actually touched. The two columns above are the counts; these are the files, and they are the input DRE-3078 sizes against.

### DRE-2676

- declared: `UNKNOWN`
- pieces touched: `UNKNOWN`

### DRE-2719

- declared: `UNKNOWN`
- pieces touched: `.github/workflows/plan.yml`, `briefs/planner.md`, `config/lane-contract.json`, `docs/lane-contract.md`, `scripts/planning_escalation.py`, `standards/card-quality.md`, `tests/test_planning_escalation.py`, `scripts/plan_run.py`, `scripts/reconcile.py`, `scripts/wave_commitment.py`, `standards/wave-plan.md`, `tests/test_epic_dependency_gate.py`, `tests/test_wave_commitment.py`, `tests/test_wave_commitment_wiring.py`, `scripts/assemble_context.py`, `scripts/plan_artifact.py`, `scripts/wave_plan.py`, `standards/README.md`, `tests/test_assemble_context.py`, `tests/test_planning_route.py`, `tests/test_wave_plan.py`, `tests/test_wave_plan_scenario.py`, `tests/test_wave_plan_wiring.py`, `tests/test_worker_pool_allowed_bots.py`, `tests/test_workflow_prompt_lanes.py`, `config/README.md`, `scripts/planning_route.py`, `config/planning-shapes.json`, `scripts/planning_shape.py`, `tests/test_planning_shape.py`

### DRE-2838

- declared: `UNKNOWN`
- pieces touched: `UNKNOWN`

### DRE-2847

- declared: `UNKNOWN`
- pieces touched: `config/lane-contract.json`, `docs/lane-contract.md`, `scripts/planning_escalation.py`, `scripts/planning_route.py`, `scripts/ready_lane_writers.py`, `standards/card-quality.md`, `tests/test_lane_contract.py`, `tests/test_no_unplanned_ready_lane_writer.py`, `.github/workflows/agent-task.yml`, `.github/workflows/medic.yml`, `.github/workflows/plan.yml`, `.github/workflows/red-main-repair.yml`, `scripts/linear_ops.py`, `scripts/validate_card.py`, `tests/test_break_glass.py`, `tests/test_oneoff_card_creation.py`, `tests/test_repo_label_validated.py`, `tests/test_validate_card_autofix.py`, `tests/test_validate_card_gate.py`, `tests/test_writers_point_at_planning.py`

### DRE-2871

- declared: `UNKNOWN`
- pieces touched: `UNKNOWN`

### DRE-2891

- declared: `UNKNOWN`
- pieces touched: `UNKNOWN`

### DRE-2937

- declared: `alerts.py`
- pieces touched: `UNKNOWN`

### DRE-3016

- declared: `scripts/planner_score.py`, `config/planner-audit.json`, `tests/test_planner_score.py`, `docs/planner-audit.md`, `plan.yml`, `planner-replay.yml`
- pieces touched: `UNKNOWN`

### DRE-3022

- declared: `scripts/split_ledger.py`, `config/split-ledger.json`, `briefs/planner.md`, `.github/workflows/plan.yml`, `reconcile.yml`, `tests/test_split_ledger.py`
- pieces touched: `UNKNOWN`

### DRE-3029

- declared: `.github/workflows/plan.yml`, `scripts/planning_shape.py`, `scripts/planning_classify.py`, `briefs/planner.md`, `tests/test_planning_classify.py`, `docs/lane-contract.md`, `lane_contract.py`
- pieces touched: `UNKNOWN`

## What could not be read

Named rather than counted, because the absence of evidence is not evidence that a card was well sized.

- **DRE-2676** — the card declares no `Files:` line, so it made no footprint claim to compare against
- **DRE-2719** — the card declares no `Files:` line, so it made no footprint claim to compare against; DRE-2847: no merged pull request this run could read
- **DRE-2838** — the card declares no `Files:` line, so it made no footprint claim to compare against; DRE-2892: this token cannot read dreadnought-foundry/agent-bureau, and an empty PR search there is indistinguishable from a card that never produced one; DRE-2891: this token cannot read dreadnought-foundry/agent-bureau, and an empty PR search there is indistinguishable from a card that never produced one; DRE-2890: this token cannot read dreadnought-foundry/agent-bureau, and an empty PR search there is indistinguishable from a card that never produced one
- **DRE-2847** — the card declares no `Files:` line, so it made no footprint claim to compare against; DRE-2860: this token cannot read dreadnought-foundry/agent-bureau, and an empty PR search there is indistinguishable from a card that never produced one
- **DRE-2871** — the card declares no `Files:` line, so it made no footprint claim to compare against; DRE-2912: this token cannot read dreadnought-foundry/agent-bureau, and an empty PR search there is indistinguishable from a card that never produced one; DRE-2911: this token cannot read dreadnought-foundry/agent-bureau, and an empty PR search there is indistinguishable from a card that never produced one; DRE-2910: this token cannot read dreadnought-foundry/agent-bureau, and an empty PR search there is indistinguishable from a card that never produced one
- **DRE-2891** — the card declares no `Files:` line, so it made no footprint claim to compare against
- **DRE-2937** — DRE-2953: this token cannot read dreadnought-foundry/agent-bureau, and an empty PR search there is indistinguishable from a card that never produced one; DRE-2952: this token cannot read dreadnought-foundry/agent-bureau, and an empty PR search there is indistinguishable from a card that never produced one
- **DRE-3022** — DRE-3079: no merged pull request this run could read; DRE-3078: no merged pull request this run could read; DRE-3077: no merged pull request this run could read
