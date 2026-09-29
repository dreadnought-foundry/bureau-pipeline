# The split ledger

<!-- GENERATED FILE — do not edit by hand. -->
<!-- Regenerate with `python3 scripts/split_ledger.py derive`. -->

Generated **2026-09-29T17:27:41Z** from Linear card bodies, labels and comment receipts, plus the merged pull requests of each card's split pieces, over a population DISCOVERED from the turn-cap, hand-back and split-citation receipts of the last 90 days plus the seed cards — with 3 read(s) that could not be made and are therefore in no count below: the search for comments carrying 'turn-exhaustion-requeue' could not be read, so any card it alone would have found is missing: The read operation timed out; the search for comments carrying 'held-for-human (turn-exhaustion-requeue cap reached)' could not be read, so any card it alone would have found is missing: The read operation timed out; the search for comments carrying '🤖 Handed back to Planning:' could not be read, so any card it alone would have found is missing: The read operation timed out.

Every card here did not fit one run: it died at the turn cap, it was split, or a build run handed it back as an epic. The point of writing it down is DRE-3022's: the planner has been sizing cards against nothing.

**The population discovers itself.** Nobody names it: the derive asks Linear for every card whose own comments carry a turn-cap or hand-back receipt, and for every card a successor cites as the one it was cut from, created in the **90 days** before that timestamp. The seed cards stay in whatever the window says, because they are here for a different reason — DRE-3077 named them.

**A read that failed says `UNKNOWN`, never 0 and never "none".** "GitHub would not say" and "the pull request touched nothing" are different facts, and a ledger that collapses them reports a history that never happened.

## The rates

21 card(s) in the ledger, 10 of which died at the turn cap at least once. They cost **$552.68** in dead runs.

15 card(s) declared no footprint at all. They are counted apart from every band below, never into one — an unread card in a denominator is a rate nobody can check.

| Declared footprint | Cards | Died | Rate |
| --- | --- | --- | --- |
| more than 1 file | 5 | 2 | 40% |
| more than 2 files | 5 | 2 | 40% |
| more than 3 files | 4 | 2 | 50% |
| more than 4 files | 4 | 2 | 50% |
| more than 5 files | 4 | 2 | 50% |
| more than 6 files | 3 | 1 | 33% |

- cards declaring more than 1 file died 2 of 5 times
- cards declaring more than 2 files died 2 of 5 times
- cards declaring more than 3 files died 2 of 4 times
- cards declaring more than 4 files died 2 of 4 times
- cards declaring more than 5 files died 2 of 4 times
- cards declaring more than 6 files died 1 of 3 times

## By month

One row per calendar month the window touches. **Planner-created children** is every card the planner gave a parent in that month — the denominator DRE-3022's split rate is measured against. **Split** and **died** are this ledger's own rows created in that month, so a card created before the window belongs to no row here.

A month is **complete** when the window covers all of it and it ended before this file was generated. An incomplete month is a PARTIAL count, not a low one — and a count that could not be read says `UNKNOWN`, never 0.

| Month | Planner-created children | Split | Died at the turn cap | Complete |
| --- | --- | --- | --- | --- |
| 2026-07 | 228 | 0 | 0 | no — a partial count |
| 2026-08 | 280 | 7 | 5 | yes |
| 2026-09 | 1221 | 8 | 5 | no — a partial count |

## The tells, in hindsight

DRE-2893's four tells, read back over each card's own body by `split_ledger.tells` — a deterministic reading of the text, not a judgement. Each one under-reports on purpose.

| Tell | What it asks | Cards | Died |
| --- | --- | --- | --- |
| `contracts-between-pieces` | Does one deliverable read what another writes? The strongest tell — if B reads what A writes it is not one card. | 5 | 2 |
| `two-languages-or-tiers` | Does the declared footprint span two languages or two tiers? Bounded is not the same as small. | 10 | 6 |
| `unenumerated-count` | Does a criterion count something the body never enumerates? DRE-2837 said "the nine derivations" and the nine were named nowhere. | 4 | 1 |
| `unbounded-quantifier` | Does the card quantify without a bound — "every surface", "all call sites"? DRE-2838's was 57 mount sites. | 8 | 2 |

## The rows

| Card | Created | Size | Role | Declared | Pieces touched | Pieces | Deaths | Cost | Tells | Why it is here |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| [DRE-2467](https://linear.app/dreadnoughtfoundry/issue/DRE-2467/epic-internal-external-roots-make-who-can-see-this-visible-in-the) | 2026-08-16T18:56:29Z | `UNKNOWN` | planner | `UNKNOWN` | `UNKNOWN` | 1 | 0 | $0.00 | `unbounded-quantifier` | `split` |
| [DRE-2676](https://linear.app/dreadnoughtfoundry/issue/DRE-2676/stop-reading-dependencies-out-of-prose-entirely-a-blocker-is-a-linear) | 2026-08-23T16:31:19Z | M | engineer | `UNKNOWN` | `UNKNOWN` | 0 | 3 | $64.38 | — | `turn-cap-death` |
| [DRE-2719](https://linear.app/dreadnoughtfoundry/issue/DRE-2719/everything-goes-to-planning-which-decides-one-off-epic-or-wave) | 2026-08-25T14:13:24Z | L | devops | `UNKNOWN` | 30 files | 6 | 2 | $42.03 | — | `turn-cap-death`, `split`, `handed-back` |
| [DRE-2744](https://linear.app/dreadnoughtfoundry/issue/DRE-2744/delete-the-two-redundant-product-keys-and-the-third-call-site-that) | 2026-08-26T17:20:10Z | `UNKNOWN` | `UNKNOWN` | `UNKNOWN` | 14 files | 3 | 0 | $0.00 | `unbounded-quantifier` | `split` |
| [DRE-2837](https://linear.app/dreadnoughtfoundry/issue/DRE-2837/one-derivation-and-no-surface-asserts-a-state-it-did-not-read) | 2026-08-29T22:55:31Z | L | engineer | `UNKNOWN` | `UNKNOWN` | 3 | 0 | $0.00 | `contracts-between-pieces`, `two-languages-or-tiers` | `split` |
| [DRE-2838](https://linear.app/dreadnoughtfoundry/issue/DRE-2838/every-rendered-claim-carries-its-age-and-the-storedlive-split-dies) | 2026-08-29T22:55:52Z | M | engineer | `UNKNOWN` | `UNKNOWN` | 3 | 4 | $81.18 | `two-languages-or-tiers`, `unbounded-quantifier` | `turn-cap-death`, `split` |
| [DRE-2842](https://linear.app/dreadnoughtfoundry/issue/DRE-2842/move-the-wave-plan-standard-into-bureau-pipelinestandards-the-wave) | 2026-08-30T20:49:16Z | XS | devops | `UNKNOWN` | `UNKNOWN` | 1 | 0 | $0.00 | `unenumerated-count`, `unbounded-quantifier` | `handed-back` |
| [DRE-2847](https://linear.app/dreadnoughtfoundry/issue/DRE-2847/close-the-back-doors-into-backlog-and-todo-and-prove-the-absence-by) | 2026-08-30T20:51:34Z | M | engineer | `UNKNOWN` | 20 files | 3 | 2 | $47.23 | `unenumerated-count`, `unbounded-quantifier` | `turn-cap-death`, `split` |
| [DRE-2871](https://linear.app/dreadnoughtfoundry/issue/DRE-2871/eight-surfaces-stop-asserting-what-they-never-read-and-unknown) | 2026-08-31T18:27:15Z | M | engineer | `UNKNOWN` | `UNKNOWN` | 3 | 6 | $105.94 | `contracts-between-pieces`, `two-languages-or-tiers` | `turn-cap-death`, `split` |
| [DRE-2891](https://linear.app/dreadnoughtfoundry/issue/DRE-2891/four-cutover-surfaces-carry-their-read-age-or-say-unknown) | 2026-09-01T00:40:52Z | M | engineer | `UNKNOWN` | `UNKNOWN` | 0 | 3 | $46.02 | `two-languages-or-tiers` | `turn-cap-death` |
| [DRE-2911](https://linear.app/dreadnoughtfoundry/issue/DRE-2911/four-backend-surfaces-stop-asserting-what-they-never-read) | 2026-09-01T03:47:22Z | S | engineer | `UNKNOWN` | `UNKNOWN` | 4 | 0 | $0.00 | `contracts-between-pieces` | `split` |
| [DRE-2912](https://linear.app/dreadnoughtfoundry/issue/DRE-2912/four-frontend-surfaces-stop-asserting-what-they-never-read) | 2026-09-01T03:47:38Z | S | engineer | `UNKNOWN` | `UNKNOWN` | 4 | 0 | $0.00 | `contracts-between-pieces` | `split` |
| [DRE-2937](https://linear.app/dreadnoughtfoundry/issue/DRE-2937/a-cold-cache-renders-nothing-needs-you-into-the-bell-and-fixing-it) | 2026-09-01T14:03:23Z | L | engineer | 1 file | `UNKNOWN` | 2 | 4 | $66.87 | `two-languages-or-tiers` | `turn-cap-death`, `split` |
| [DRE-3012](https://linear.app/dreadnoughtfoundry/issue/DRE-3012/the-format-has-no-dropdown-50-of-the-questionnaires-controls-are) | 2026-09-03T19:46:13Z | M | engineer | `UNKNOWN` | `UNKNOWN` | 3 | 2 | $37.24 | — | `turn-cap-death`, `split` |
| [DRE-3016](https://linear.app/dreadnoughtfoundry/issue/DRE-3016/score-the-planner-against-plans-it-has-never-seen-replay-past-epics) | 2026-09-04T00:28:19Z | L | engineer | 6 files | `UNKNOWN` | 0 | 1 | $22.57 | `two-languages-or-tiers` | `turn-cap-death` |
| [DRE-3022](https://linear.app/dreadnoughtfoundry/issue/DRE-3022/the-planner-sizes-against-the-ledger-every-turn-cap-death-split-and) | 2026-09-04T00:33:40Z | M | engineer | 7 files | 12 files | 3 | 2 | $39.22 | `contracts-between-pieces`, `two-languages-or-tiers` | `turn-cap-death` |
| [DRE-3029](https://linear.app/dreadnoughtfoundry/issue/DRE-3029/planning-classifies-the-card-itself-the-planner-run-stamps-one-off) | 2026-09-04T00:40:47Z | M | engineer | 7 files | `UNKNOWN` | 0 | 0 | $0.00 | `two-languages-or-tiers`, `unenumerated-count` | `named-as-a-seed` |
| [DRE-3213](https://linear.app/dreadnoughtfoundry/issue/DRE-3213/agent-bureau-releasejson-gains-the-website-and-relay-entries-with-auto) | 2026-09-05T21:19:56Z | S | engineer | 3 files | `UNKNOWN` | 1 | 0 | $0.00 | `two-languages-or-tiers`, `unbounded-quantifier` | `split` |
| [DRE-4297](https://linear.app/dreadnoughtfoundry/issue/DRE-4297/the-record-contract-declares-the-heartbeat-its-event-name-and-delivery) | 2026-09-19T05:55:12Z | `UNKNOWN` | engineer | 7 files | `UNKNOWN` | 1 | 0 | $0.00 | — | `split` |
| [DRE-4322](https://linear.app/dreadnoughtfoundry/issue/DRE-4322/bureau-pipeline-a-repo-with-no-fix-agent-and-a-held-pull-request-is) | 2026-09-19T17:11:26Z | `UNKNOWN` | engineer | `UNKNOWN` | 16 files | 2 | 0 | $0.00 | `two-languages-or-tiers`, `unenumerated-count`, `unbounded-quantifier` | `split`, `handed-back` |
| [DRE-5131](https://linear.app/dreadnoughtfoundry/issue/DRE-5131/agent-bureau-the-0630-pt-briefing-lists-the-epics-in-motion-in) | 2026-09-28T22:16:16Z | `UNKNOWN` | engineer | `UNKNOWN` | `UNKNOWN` | 1 | 0 | $0.00 | `unbounded-quantifier` | `split` |

## The footprints

What each card SAID it would touch, against what its pieces actually touched. The two columns above are the counts; these are the files, and they are the input DRE-3078 sizes against.

### DRE-2467

- declared: `UNKNOWN`
- pieces touched: `UNKNOWN`

### DRE-2676

- declared: `UNKNOWN`
- pieces touched: `UNKNOWN`

### DRE-2719

- declared: `UNKNOWN`
- pieces touched: `.github/workflows/plan.yml`, `briefs/planner.md`, `config/lane-contract.json`, `docs/lane-contract.md`, `scripts/planning_escalation.py`, `standards/card-quality.md`, `tests/test_planning_escalation.py`, `scripts/plan_run.py`, `scripts/reconcile.py`, `scripts/wave_commitment.py`, `standards/wave-plan.md`, `tests/test_epic_dependency_gate.py`, `tests/test_wave_commitment.py`, `tests/test_wave_commitment_wiring.py`, `scripts/assemble_context.py`, `scripts/plan_artifact.py`, `scripts/wave_plan.py`, `standards/README.md`, `tests/test_assemble_context.py`, `tests/test_planning_route.py`, `tests/test_wave_plan.py`, `tests/test_wave_plan_scenario.py`, `tests/test_wave_plan_wiring.py`, `tests/test_worker_pool_allowed_bots.py`, `tests/test_workflow_prompt_lanes.py`, `config/README.md`, `scripts/planning_route.py`, `config/planning-shapes.json`, `scripts/planning_shape.py`, `tests/test_planning_shape.py`

### DRE-2744

- declared: `UNKNOWN`
- pieces touched: `README.md`, `briefs/planner.md`, `config/README.md`, `scripts/linear_ops.py`, `scripts/structural_repair.py`, `scripts/validate_card.py`, `standards/card-quality.md`, `tests/test_initiative_claim_matches_the_code.py`, `tests/test_oneoff_card_creation.py`, `tests/test_repo_map_snapshot.py`, `tests/test_structural_repair.py`, `tests/test_subissue_valid_children.py`, `tests/test_validate_card.py`, `tests/test_validate_card_autofix.py`

### DRE-2837

- declared: `UNKNOWN`
- pieces touched: `UNKNOWN`

### DRE-2838

- declared: `UNKNOWN`
- pieces touched: `UNKNOWN`

### DRE-2842

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

### DRE-2911

- declared: `UNKNOWN`
- pieces touched: `UNKNOWN`

### DRE-2912

- declared: `UNKNOWN`
- pieces touched: `UNKNOWN`

### DRE-2937

- declared: `alerts.py`
- pieces touched: `UNKNOWN`

### DRE-3012

- declared: `UNKNOWN`
- pieces touched: `UNKNOWN`

### DRE-3016

- declared: `scripts/planner_score.py`, `config/planner-audit.json`, `tests/test_planner_score.py`, `docs/planner-audit.md`, `plan.yml`, `planner-replay.yml`
- pieces touched: `UNKNOWN`

### DRE-3022

- declared: `scripts/split_ledger.py`, `config/split-ledger.json`, `briefs/planner.md`, `.github/workflows/plan.yml`, `reconcile.yml`, `tests/test_split_ledger.py`, `scripts/mid_epic.py`
- pieces touched: `config/README.md`, `config/planner-audit.json`, `docs/planner-audit.md`, `scripts/plan_critic.py`, `scripts/planner_score.py`, `scripts/split_ledger.py`, `standards/plan-critic.md`, `tests/test_plan_critic.py`, `tests/test_planner_score.py`, `config/split-ledger.json`, `docs/split-ledger.md`, `tests/test_split_ledger.py`

### DRE-3029

- declared: `.github/workflows/plan.yml`, `scripts/planning_shape.py`, `scripts/planning_classify.py`, `briefs/planner.md`, `tests/test_planning_classify.py`, `docs/lane-contract.md`, `lane_contract.py`
- pieces touched: `UNKNOWN`

### DRE-3213

- declared: `.github/bureau/release.json`, `scripts/release_surfaces.py`, `scripts/test_release_surfaces.py`
- pieces touched: `UNKNOWN`

### DRE-4297

- declared: `config/record-contract.json`, `scripts/sync_repo_mirrors.py`, `cloud/relay-gh/record_contract.py`, `console/backend/record_contract.py`, `cloud/record-jobs/record_contract.py`, `infra/lambda/record-heartbeat/record_contract.py`, `console/backend/tests/test_record_contract.py`
- pieces touched: `UNKNOWN`

### DRE-4322

- declared: `UNKNOWN`
- pieces touched: `config/pipeline-acts.json`, `docs/held-pr-recovery.md`, `docs/pipeline-acts.md`, `scripts/reconcile.py`, `tests/fixtures/act-receipt-bodies.json`, `tests/test_act_cadence.py`, `tests/test_act_emission.py`, `tests/test_conflict_sweep_per_pr_busy.py`, `tests/test_decision_answers_the_verdict.py`, `tests/test_hand_dispatch_no_work.py`, `tests/test_linear_sync_workflow.py`, `tests/test_operator_decision_restart.py`, `tests/test_pipeline_acts.py`, `tests/test_redispatch_standing_verdict.py`, `tests/test_restart_instruction_matches_gate.py`, `tests/test_verdict_sha_binding.py`

### DRE-5131

- declared: `UNKNOWN`
- pieces touched: `UNKNOWN`

## What could not be read

Named rather than counted, because the absence of evidence is not evidence that a card was well sized.

- **DRE-2467** — the card declares no `Files:` line, so it made no footprint claim to compare against; DRE-2468: this token cannot read dreadnought-foundry/portico, and an empty PR search there is indistinguishable from a card that never produced one
- **DRE-2676** — the card declares no `Files:` line, so it made no footprint claim to compare against
- **DRE-2719** — the card declares no `Files:` line, so it made no footprint claim to compare against; DRE-2847: no merged pull request this run could read
- **DRE-2744** — the card declares no `Files:` line, so it made no footprint claim to compare against; DRE-2876: this token cannot read dreadnought-foundry/agent-bureau, and an empty PR search there is indistinguishable from a card that never produced one; DRE-2875: this token cannot read dreadnought-foundry/agent-bureau, and an empty PR search there is indistinguishable from a card that never produced one
- **DRE-2837** — the card declares no `Files:` line, so it made no footprint claim to compare against; DRE-2872: this token cannot read dreadnought-foundry/agent-bureau, and an empty PR search there is indistinguishable from a card that never produced one; DRE-2871: this token cannot read dreadnought-foundry/agent-bureau, and an empty PR search there is indistinguishable from a card that never produced one; DRE-2870: this token cannot read dreadnought-foundry/agent-bureau, and an empty PR search there is indistinguishable from a card that never produced one
- **DRE-2838** — the card declares no `Files:` line, so it made no footprint claim to compare against; DRE-2892: this token cannot read dreadnought-foundry/agent-bureau, and an empty PR search there is indistinguishable from a card that never produced one; DRE-2891: this token cannot read dreadnought-foundry/agent-bureau, and an empty PR search there is indistinguishable from a card that never produced one; DRE-2890: this token cannot read dreadnought-foundry/agent-bureau, and an empty PR search there is indistinguishable from a card that never produced one
- **DRE-2842** — the card declares no `Files:` line, so it made no footprint claim to compare against; DRE-2851: this token cannot read dreadnought-foundry/agent-bureau, and an empty PR search there is indistinguishable from a card that never produced one
- **DRE-2847** — the card declares no `Files:` line, so it made no footprint claim to compare against; DRE-2860: this token cannot read dreadnought-foundry/agent-bureau, and an empty PR search there is indistinguishable from a card that never produced one
- **DRE-2871** — the card declares no `Files:` line, so it made no footprint claim to compare against; DRE-2912: this token cannot read dreadnought-foundry/agent-bureau, and an empty PR search there is indistinguishable from a card that never produced one; DRE-2911: this token cannot read dreadnought-foundry/agent-bureau, and an empty PR search there is indistinguishable from a card that never produced one; DRE-2910: this token cannot read dreadnought-foundry/agent-bureau, and an empty PR search there is indistinguishable from a card that never produced one
- **DRE-2891** — the card declares no `Files:` line, so it made no footprint claim to compare against
- **DRE-2911** — the card declares no `Files:` line, so it made no footprint claim to compare against; DRE-2937: this token cannot read dreadnought-foundry/agent-bureau, and an empty PR search there is indistinguishable from a card that never produced one; DRE-2936: this token cannot read dreadnought-foundry/agent-bureau, and an empty PR search there is indistinguishable from a card that never produced one; DRE-2935: this token cannot read dreadnought-foundry/agent-bureau, and an empty PR search there is indistinguishable from a card that never produced one; DRE-2934: this token cannot read dreadnought-foundry/agent-bureau, and an empty PR search there is indistinguishable from a card that never produced one
- **DRE-2912** — the card declares no `Files:` line, so it made no footprint claim to compare against; DRE-2942: this token cannot read dreadnought-foundry/agent-bureau, and an empty PR search there is indistinguishable from a card that never produced one; DRE-2941: this token cannot read dreadnought-foundry/agent-bureau, and an empty PR search there is indistinguishable from a card that never produced one; DRE-2940: this token cannot read dreadnought-foundry/agent-bureau, and an empty PR search there is indistinguishable from a card that never produced one; DRE-2939: this token cannot read dreadnought-foundry/agent-bureau, and an empty PR search there is indistinguishable from a card that never produced one
- **DRE-2937** — DRE-2953: this token cannot read dreadnought-foundry/agent-bureau, and an empty PR search there is indistinguishable from a card that never produced one; DRE-2952: this token cannot read dreadnought-foundry/agent-bureau, and an empty PR search there is indistinguishable from a card that never produced one
- **DRE-3012** — the card declares no `Files:` line, so it made no footprint claim to compare against; DRE-3025: this token cannot read dreadnought-foundry/portico, and an empty PR search there is indistinguishable from a card that never produced one; DRE-3024: this token cannot read dreadnought-foundry/portico, and an empty PR search there is indistinguishable from a card that never produced one; DRE-3023: this token cannot read dreadnought-foundry/portico, and an empty PR search there is indistinguishable from a card that never produced one
- **DRE-3022** — DRE-3078: no merged pull request this run could read
- **DRE-3213** — DRE-3238: this token cannot read dreadnought-foundry/agent-bureau, and an empty PR search there is indistinguishable from a card that never produced one
- **DRE-4297** — DRE-4298: this token cannot read dreadnought-foundry/agent-bureau, and an empty PR search there is indistinguishable from a card that never produced one
- **DRE-4322** — the card declares no `Files:` line, so it made no footprint claim to compare against; DRE-4377: this token cannot read dreadnought-foundry/agent-bureau, and an empty PR search there is indistinguishable from a card that never produced one
- **DRE-5131** — the card declares no `Files:` line, so it made no footprint claim to compare against; DRE-5140: this token cannot read dreadnought-foundry/agent-bureau, and an empty PR search there is indistinguishable from a card that never produced one
