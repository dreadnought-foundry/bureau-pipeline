# The split ledger

<!-- GENERATED FILE — do not edit by hand. -->
<!-- Regenerate with `python3 scripts/split_ledger.py derive`. -->

Generated **2026-09-30T04:52:54Z** from Linear card bodies, labels and comment receipts, plus the merged pull requests of each card's split pieces, over a population DISCOVERED from the turn-cap, hand-back and split-citation receipts of the last 90 days plus the seed cards — with 1 read(s) that could not be made and are therefore in no count below: the search for comments carrying 'turn-exhaustion-requeue' could not be read, so any card it alone would have found is missing: The read operation timed out.

Every card here did not fit one run: it died at the turn cap, it was split, or a build run handed it back as an epic. The point of writing it down is DRE-3022's: the planner has been sizing cards against nothing.

**The population discovers itself.** Nobody names it: the derive asks Linear for every card whose own comments carry a turn-cap or hand-back receipt, and for every card a successor cites as the one it was cut from, created in the **90 days** before that timestamp. The seed cards stay in whatever the window says, because they are here for a different reason — DRE-3077 named them.

**A read that failed says `UNKNOWN`, never 0 and never "none".** "GitHub would not say" and "the pull request touched nothing" are different facts, and a ledger that collapses them reports a history that never happened.

## The rates

35 card(s) in the ledger, 20 of which died at the turn cap at least once. They cost **$994.90** in dead runs.

24 card(s) declared no footprint at all. They are counted apart from every band below, never into one — an unread card in a denominator is a rate nobody can check.

| Declared footprint | Cards | Died | Rate |
| --- | --- | --- | --- |
| more than 1 file | 10 | 4 | 40% |
| more than 2 files | 8 | 3 | 38% |
| more than 3 files | 6 | 2 | 33% |
| more than 4 files | 6 | 2 | 33% |
| more than 5 files | 6 | 2 | 33% |
| more than 6 files | 4 | 1 | 25% |
| more than 7 files | 1 | 0 | 0% |
| more than 8 files | 1 | 0 | 0% |
| more than 9 files | 1 | 0 | 0% |
| more than 10 files | 1 | 0 | 0% |
| more than 11 files | 1 | 0 | 0% |
| more than 12 files | 1 | 0 | 0% |

- cards declaring more than 1 file died 4 of 10 times
- cards declaring more than 2 files died 3 of 8 times
- cards declaring more than 3 files died 2 of 6 times
- cards declaring more than 4 files died 2 of 6 times
- cards declaring more than 5 files died 2 of 6 times
- cards declaring more than 6 files died 1 of 4 times
- cards declaring more than 7 files died 0 of 1 times
- cards declaring more than 8 files died 0 of 1 times
- cards declaring more than 9 files died 0 of 1 times
- cards declaring more than 10 files died 0 of 1 times
- cards declaring more than 11 files died 0 of 1 times
- cards declaring more than 12 files died 0 of 1 times

## By month

One row per calendar month the window touches. **Planner-created children** is every card the planner gave a parent in that month — the denominator DRE-3022's split rate is measured against. **Split** and **died** are this ledger's own rows created in that month, so a card created before the window belongs to no row here.

A month is **complete** when the window covers all of it and it ended before this file was generated. An incomplete month is a PARTIAL count, not a low one — and a count that could not be read says `UNKNOWN`, never 0.

| Month | Planner-created children | Split | Died at the turn cap | Complete |
| --- | --- | --- | --- | --- |
| 2026-07 | 191 | 0 | 0 | no — a partial count |
| 2026-08 | 280 | 7 | 10 | yes |
| 2026-09 | 1256 | 10 | 10 | no — a partial count |

## The tells, in hindsight

DRE-2893's four tells, read back over each card's own body by `split_ledger.tells` — a deterministic reading of the text, not a judgement. Each one under-reports on purpose.

| Tell | What it asks | Cards | Died |
| --- | --- | --- | --- |
| `contracts-between-pieces` | Does one deliverable read what another writes? The strongest tell — if B reads what A writes it is not one card. | 6 | 3 |
| `two-languages-or-tiers` | Does the declared footprint span two languages or two tiers? Bounded is not the same as small. | 16 | 9 |
| `unenumerated-count` | Does a criterion count something the body never enumerates? DRE-2837 said "the nine derivations" and the nine were named nowhere. | 4 | 1 |
| `unbounded-quantifier` | Does the card quantify without a bound — "every surface", "all call sites"? DRE-2838's was 57 mount sites. | 13 | 4 |

## The rows

| Card | Created | Size | Role | Declared | Pieces touched | Pieces | Deaths | Cost | Tells | Why it is here |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| [DRE-2467](https://linear.app/dreadnoughtfoundry/issue/DRE-2467/epic-internal-external-roots-make-who-can-see-this-visible-in-the) | 2026-08-16T18:56:29Z | `UNKNOWN` | planner | `UNKNOWN` | `UNKNOWN` | 1 | 0 | $0.00 | `unbounded-quantifier` | `split` |
| [DRE-2676](https://linear.app/dreadnoughtfoundry/issue/DRE-2676/stop-reading-dependencies-out-of-prose-entirely-a-blocker-is-a-linear) | 2026-08-23T16:31:19Z | M | engineer | `UNKNOWN` | `UNKNOWN` | 0 | 3 | $64.38 | — | `turn-cap-death` |
| [DRE-2719](https://linear.app/dreadnoughtfoundry/issue/DRE-2719/everything-goes-to-planning-which-decides-one-off-epic-or-wave) | 2026-08-25T14:13:24Z | L | devops | `UNKNOWN` | 30 files | 6 | 2 | $42.03 | — | `turn-cap-death`, `split`, `handed-back` |
| [DRE-2744](https://linear.app/dreadnoughtfoundry/issue/DRE-2744/delete-the-two-redundant-product-keys-and-the-third-call-site-that) | 2026-08-26T17:20:10Z | `UNKNOWN` | `UNKNOWN` | `UNKNOWN` | 14 files | 3 | 0 | $0.00 | `unbounded-quantifier` | `split` |
| [DRE-2771](https://linear.app/dreadnoughtfoundry/issue/DRE-2771/phase-1s-two-lane-changes-were-never-declared-the-board-still-has-no) | 2026-08-27T00:42:33Z | M | engineer | `UNKNOWN` | `UNKNOWN` | 0 | 2 | $36.89 | — | `turn-cap-death` |
| [DRE-2811](https://linear.app/dreadnoughtfoundry/issue/DRE-2811/the-console-cannot-tell-a-working-fix-loop-from-a-stalled-one-both) | 2026-08-29T03:58:21Z | M | devops | `UNKNOWN` | `UNKNOWN` | 0 | 3 | $52.84 | — | `turn-cap-death` |
| [DRE-2814](https://linear.app/dreadnoughtfoundry/issue/DRE-2814/console-noise-the-dashboard-keeps-asking-for-a-human-who-already) | 2026-08-29T15:42:18Z | M | devops | `UNKNOWN` | `UNKNOWN` | 0 | 4 | $72.01 | `unbounded-quantifier` | `turn-cap-death` |
| [DRE-2821](https://linear.app/dreadnoughtfoundry/issue/DRE-2821/every-automatic-recovery-is-invisible-to-the-console-it-shows-the-last) | 2026-08-29T17:20:58Z | M | devops | `UNKNOWN` | `UNKNOWN` | 0 | 2 | $36.54 | `two-languages-or-tiers`, `unbounded-quantifier` | `turn-cap-death` |
| [DRE-2837](https://linear.app/dreadnoughtfoundry/issue/DRE-2837/one-derivation-and-no-surface-asserts-a-state-it-did-not-read) | 2026-08-29T22:55:31Z | L | engineer | `UNKNOWN` | `UNKNOWN` | 3 | 0 | $0.00 | `contracts-between-pieces`, `two-languages-or-tiers` | `split` |
| [DRE-2838](https://linear.app/dreadnoughtfoundry/issue/DRE-2838/every-rendered-claim-carries-its-age-and-the-storedlive-split-dies) | 2026-08-29T22:55:52Z | M | engineer | `UNKNOWN` | `UNKNOWN` | 3 | 4 | $81.18 | `two-languages-or-tiers`, `unbounded-quantifier` | `turn-cap-death`, `split` |
| [DRE-2842](https://linear.app/dreadnoughtfoundry/issue/DRE-2842/move-the-wave-plan-standard-into-bureau-pipelinestandards-the-wave) | 2026-08-30T20:49:16Z | XS | devops | `UNKNOWN` | `UNKNOWN` | 1 | 0 | $0.00 | `unenumerated-count`, `unbounded-quantifier` | `handed-back` |
| [DRE-2847](https://linear.app/dreadnoughtfoundry/issue/DRE-2847/close-the-back-doors-into-backlog-and-todo-and-prove-the-absence-by) | 2026-08-30T20:51:34Z | M | engineer | `UNKNOWN` | 20 files | 3 | 2 | $47.23 | `unenumerated-count`, `unbounded-quantifier` | `turn-cap-death`, `split` |
| [DRE-2852](https://linear.app/dreadnoughtfoundry/issue/DRE-2852/moving-work-looks-like-it-is-moving-a-pulse-earned-from-a-timestamp) | 2026-08-30T22:00:55Z | S | engineer | `UNKNOWN` | `UNKNOWN` | 0 | 2 | $32.89 | — | `turn-cap-death` |
| [DRE-2871](https://linear.app/dreadnoughtfoundry/issue/DRE-2871/eight-surfaces-stop-asserting-what-they-never-read-and-unknown) | 2026-08-31T18:27:15Z | M | engineer | `UNKNOWN` | `UNKNOWN` | 3 | 6 | $105.94 | `contracts-between-pieces`, `two-languages-or-tiers` | `turn-cap-death`, `split` |
| [DRE-2891](https://linear.app/dreadnoughtfoundry/issue/DRE-2891/four-cutover-surfaces-carry-their-read-age-or-say-unknown) | 2026-09-01T00:40:52Z | M | engineer | `UNKNOWN` | `UNKNOWN` | 0 | 3 | $46.02 | `two-languages-or-tiers` | `turn-cap-death` |
| [DRE-2898](https://linear.app/dreadnoughtfoundry/issue/DRE-2898/compliance-an-answer-to-a-question-the-form-never-asked-exports-as-an) | 2026-09-01T01:02:38Z | S | engineer | `UNKNOWN` | `UNKNOWN` | 0 | 2 | $39.47 | — | `turn-cap-death` |
| [DRE-2911](https://linear.app/dreadnoughtfoundry/issue/DRE-2911/four-backend-surfaces-stop-asserting-what-they-never-read) | 2026-09-01T03:47:22Z | S | engineer | `UNKNOWN` | `UNKNOWN` | 4 | 0 | $0.00 | `contracts-between-pieces` | `split` |
| [DRE-2912](https://linear.app/dreadnoughtfoundry/issue/DRE-2912/four-frontend-surfaces-stop-asserting-what-they-never-read) | 2026-09-01T03:47:38Z | S | engineer | `UNKNOWN` | `UNKNOWN` | 4 | 0 | $0.00 | `contracts-between-pieces` | `split` |
| [DRE-2937](https://linear.app/dreadnoughtfoundry/issue/DRE-2937/a-cold-cache-renders-nothing-needs-you-into-the-bell-and-fixing-it) | 2026-09-01T14:03:23Z | L | engineer | 1 file | `UNKNOWN` | 2 | 4 | $66.87 | `two-languages-or-tiers` | `turn-cap-death`, `split` |
| [DRE-2968](https://linear.app/dreadnoughtfoundry/issue/DRE-2968/the-dev-mock-has-no-artifact-with-painted-anchors-so-no-passage) | 2026-09-02T12:09:28Z | S | frontend | `UNKNOWN` | `UNKNOWN` | 0 | 2 | $37.50 | — | `turn-cap-death` |
| [DRE-3012](https://linear.app/dreadnoughtfoundry/issue/DRE-3012/the-format-has-no-dropdown-50-of-the-questionnaires-controls-are) | 2026-09-03T19:46:13Z | M | engineer | `UNKNOWN` | `UNKNOWN` | 3 | 2 | $37.24 | — | `turn-cap-death`, `split` |
| [DRE-3016](https://linear.app/dreadnoughtfoundry/issue/DRE-3016/score-the-planner-against-plans-it-has-never-seen-replay-past-epics) | 2026-09-04T00:28:19Z | L | engineer | 6 files | `UNKNOWN` | 0 | 1 | $22.57 | `two-languages-or-tiers` | `turn-cap-death` |
| [DRE-3022](https://linear.app/dreadnoughtfoundry/issue/DRE-3022/the-planner-sizes-against-the-ledger-every-turn-cap-death-split-and) | 2026-09-04T00:33:40Z | M | engineer | 7 files | 12 files | 3 | 2 | $39.22 | `contracts-between-pieces`, `two-languages-or-tiers` | `turn-cap-death` |
| [DRE-3029](https://linear.app/dreadnoughtfoundry/issue/DRE-3029/planning-classifies-the-card-itself-the-planner-run-stamps-one-off) | 2026-09-04T00:40:47Z | M | engineer | 7 files | `UNKNOWN` | 0 | 0 | $0.00 | `two-languages-or-tiers`, `unenumerated-count` | `named-as-a-seed` |
| [DRE-3037](https://linear.app/dreadnoughtfoundry/issue/DRE-3037/a-placed-form-freezes-a-full-copy-of-its-definition-into-the-library) | 2026-09-04T00:50:48Z | M | engineer | `UNKNOWN` | `UNKNOWN` | 0 | 2 | $39.34 | — | `turn-cap-death` |
| [DRE-3078](https://linear.app/dreadnoughtfoundry/issue/DRE-3078/the-planner-reads-the-split-ledger-and-the-mulch-planning-records) | 2026-09-04T04:55:00Z | S | devops | 3 files | `UNKNOWN` | 0 | 2 | $40.07 | `contracts-between-pieces`, `two-languages-or-tiers` | `turn-cap-death` |
| [DRE-3088](https://linear.app/dreadnoughtfoundry/issue/DRE-3088/the-post-approval-send-back-revises-the-plan-and-never-builds-a-plan) | 2026-09-04T15:44:48Z | XS | devops | 2 files | `UNKNOWN` | 0 | 3 | $54.67 | `two-languages-or-tiers` | `turn-cap-death` |
| [DRE-3213](https://linear.app/dreadnoughtfoundry/issue/DRE-3213/agent-bureau-releasejson-gains-the-website-and-relay-entries-with-auto) | 2026-09-05T21:19:56Z | S | engineer | 3 files | `UNKNOWN` | 1 | 0 | $0.00 | `two-languages-or-tiers`, `unbounded-quantifier` | `split` |
| [DRE-3306](https://linear.app/dreadnoughtfoundry/issue/DRE-3306/one-river-atomic-rebuild-tier-4-the-templates-and-the-showroom-hand) | 2026-09-08T00:41:56Z | L | frontend | 13 files | `UNKNOWN` | 0 | 0 | $0.00 | `two-languages-or-tiers` | `handed-back` |
| [DRE-4297](https://linear.app/dreadnoughtfoundry/issue/DRE-4297/the-record-contract-declares-the-heartbeat-its-event-name-and-delivery) | 2026-09-19T05:55:12Z | `UNKNOWN` | engineer | 7 files | `UNKNOWN` | 1 | 0 | $0.00 | — | `split` |
| [DRE-4322](https://linear.app/dreadnoughtfoundry/issue/DRE-4322/bureau-pipeline-a-repo-with-no-fix-agent-and-a-held-pull-request-is) | 2026-09-19T17:11:26Z | `UNKNOWN` | engineer | `UNKNOWN` | 16 files | 2 | 0 | $0.00 | `two-languages-or-tiers`, `unenumerated-count`, `unbounded-quantifier` | `split`, `handed-back` |
| [DRE-4401](https://linear.app/dreadnoughtfoundry/issue/DRE-4401/agent-bureau-every-pool-membership-change-lands-in-the-record) | 2026-09-20T19:36:00Z | `UNKNOWN` | planner | `UNKNOWN` | `UNKNOWN` | 0 | 0 | $0.00 | `two-languages-or-tiers`, `unbounded-quantifier` | `handed-back` |
| [DRE-5010](https://linear.app/dreadnoughtfoundry/issue/DRE-5010/portico-the-engine-gains-a-documents-arm-the-post-deploy-migrations) | 2026-09-27T14:45:16Z | L | engineer | 2 files | `UNKNOWN` | 1 | 0 | $0.00 | `unbounded-quantifier` | `split` |
| [DRE-5131](https://linear.app/dreadnoughtfoundry/issue/DRE-5131/agent-bureau-the-0630-pt-briefing-lists-the-epics-in-motion-in) | 2026-09-28T22:16:16Z | `UNKNOWN` | engineer | `UNKNOWN` | `UNKNOWN` | 1 | 0 | $0.00 | `unbounded-quantifier` | `split` |
| [DRE-5280](https://linear.app/dreadnoughtfoundry/issue/DRE-5280/bureau-pipeline-planyml-runs-the-second-critic-before-green-light-a) | 2026-09-30T02:10:53Z | `UNKNOWN` | devops | 6 files | `UNKNOWN` | 1 | 0 | $0.00 | `two-languages-or-tiers`, `unbounded-quantifier` | `split` |

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

### DRE-2771

- declared: `UNKNOWN`
- pieces touched: `UNKNOWN`

### DRE-2811

- declared: `UNKNOWN`
- pieces touched: `UNKNOWN`

### DRE-2814

- declared: `UNKNOWN`
- pieces touched: `UNKNOWN`

### DRE-2821

- declared: `UNKNOWN`
- pieces touched: `UNKNOWN`

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

### DRE-2852

- declared: `UNKNOWN`
- pieces touched: `UNKNOWN`

### DRE-2871

- declared: `UNKNOWN`
- pieces touched: `UNKNOWN`

### DRE-2891

- declared: `UNKNOWN`
- pieces touched: `UNKNOWN`

### DRE-2898

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

### DRE-2968

- declared: `UNKNOWN`
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

### DRE-3037

- declared: `UNKNOWN`
- pieces touched: `UNKNOWN`

### DRE-3078

- declared: `.github/workflows/plan.yml`, `briefs/planner.md`, `scripts/plan_artifact.py`
- pieces touched: `UNKNOWN`

### DRE-3088

- declared: `.github/workflows/plan.yml`, `tests/test_plan_workflow.py`
- pieces touched: `UNKNOWN`

### DRE-3213

- declared: `.github/bureau/release.json`, `scripts/release_surfaces.py`, `scripts/test_release_surfaces.py`
- pieces touched: `UNKNOWN`

### DRE-3306

- declared: `console/web/src/components/oneriver/`, `console/web/src/components/oneriver/atomic.guard.test.ts`, `console/web/src/components/alerts/AlertTable.tsx`, `console/web/tailwind.config.ts`, `console/web/src/theme/stage.token.test.ts`, `console/web/src/pages/Gallery.tsx`, `gallery/OneRiverSpecimens.tsx`, `console/web/src/dev/`, `console/web/src/lib/graphqlClient.ts`, `console/design/atomic/`, `templates.html`, `index.html`, `coverage.md`
- pieces touched: `UNKNOWN`

### DRE-4297

- declared: `config/record-contract.json`, `scripts/sync_repo_mirrors.py`, `cloud/relay-gh/record_contract.py`, `console/backend/record_contract.py`, `cloud/record-jobs/record_contract.py`, `infra/lambda/record-heartbeat/record_contract.py`, `console/backend/tests/test_record_contract.py`
- pieces touched: `UNKNOWN`

### DRE-4322

- declared: `UNKNOWN`
- pieces touched: `config/pipeline-acts.json`, `docs/held-pr-recovery.md`, `docs/pipeline-acts.md`, `scripts/reconcile.py`, `tests/fixtures/act-receipt-bodies.json`, `tests/test_act_cadence.py`, `tests/test_act_emission.py`, `tests/test_conflict_sweep_per_pr_busy.py`, `tests/test_decision_answers_the_verdict.py`, `tests/test_hand_dispatch_no_work.py`, `tests/test_linear_sync_workflow.py`, `tests/test_operator_decision_restart.py`, `tests/test_pipeline_acts.py`, `tests/test_redispatch_standing_verdict.py`, `tests/test_restart_instruction_matches_gate.py`, `tests/test_verdict_sha_binding.py`

### DRE-4401

- declared: `UNKNOWN`
- pieces touched: `UNKNOWN`

### DRE-5010

- declared: `infra/scripts/lib/release-engine.sh`, `infra/test/release_engine_documents.test.ts`
- pieces touched: `UNKNOWN`

### DRE-5131

- declared: `UNKNOWN`
- pieces touched: `UNKNOWN`

### DRE-5280

- declared: `.github/workflows/plan.yml`, `config/pipeline-acts.json`, `scripts/plan_critic.py`, `tests/test_plan_critic.py`, `tests/test_plan_critic_wiring.py`, `tests/test_plan_token_remint.py`
- pieces touched: `UNKNOWN`

## What could not be read

Named rather than counted, because the absence of evidence is not evidence that a card was well sized.

- **DRE-2467** — the card declares no `Files:` line, so it made no footprint claim to compare against; DRE-2468: this token cannot read dreadnought-foundry/portico, and an empty PR search there is indistinguishable from a card that never produced one
- **DRE-2676** — the card declares no `Files:` line, so it made no footprint claim to compare against
- **DRE-2719** — the card declares no `Files:` line, so it made no footprint claim to compare against; DRE-2847: no merged pull request this run could read
- **DRE-2744** — the card declares no `Files:` line, so it made no footprint claim to compare against; DRE-2876: this token cannot read dreadnought-foundry/agent-bureau, and an empty PR search there is indistinguishable from a card that never produced one; DRE-2875: this token cannot read dreadnought-foundry/agent-bureau, and an empty PR search there is indistinguishable from a card that never produced one
- **DRE-2771** — the card declares no `Files:` line, so it made no footprint claim to compare against
- **DRE-2811** — the card declares no `Files:` line, so it made no footprint claim to compare against
- **DRE-2814** — the card declares no `Files:` line, so it made no footprint claim to compare against
- **DRE-2821** — the card declares no `Files:` line, so it made no footprint claim to compare against
- **DRE-2837** — the card declares no `Files:` line, so it made no footprint claim to compare against; DRE-2872: this token cannot read dreadnought-foundry/agent-bureau, and an empty PR search there is indistinguishable from a card that never produced one; DRE-2871: this token cannot read dreadnought-foundry/agent-bureau, and an empty PR search there is indistinguishable from a card that never produced one; DRE-2870: this token cannot read dreadnought-foundry/agent-bureau, and an empty PR search there is indistinguishable from a card that never produced one
- **DRE-2838** — the card declares no `Files:` line, so it made no footprint claim to compare against; DRE-2892: this token cannot read dreadnought-foundry/agent-bureau, and an empty PR search there is indistinguishable from a card that never produced one; DRE-2891: this token cannot read dreadnought-foundry/agent-bureau, and an empty PR search there is indistinguishable from a card that never produced one; DRE-2890: this token cannot read dreadnought-foundry/agent-bureau, and an empty PR search there is indistinguishable from a card that never produced one
- **DRE-2842** — the card declares no `Files:` line, so it made no footprint claim to compare against; DRE-2851: this token cannot read dreadnought-foundry/agent-bureau, and an empty PR search there is indistinguishable from a card that never produced one
- **DRE-2847** — the card declares no `Files:` line, so it made no footprint claim to compare against; DRE-2860: this token cannot read dreadnought-foundry/agent-bureau, and an empty PR search there is indistinguishable from a card that never produced one
- **DRE-2852** — the card declares no `Files:` line, so it made no footprint claim to compare against
- **DRE-2871** — the card declares no `Files:` line, so it made no footprint claim to compare against; DRE-2912: this token cannot read dreadnought-foundry/agent-bureau, and an empty PR search there is indistinguishable from a card that never produced one; DRE-2911: this token cannot read dreadnought-foundry/agent-bureau, and an empty PR search there is indistinguishable from a card that never produced one; DRE-2910: this token cannot read dreadnought-foundry/agent-bureau, and an empty PR search there is indistinguishable from a card that never produced one
- **DRE-2891** — the card declares no `Files:` line, so it made no footprint claim to compare against
- **DRE-2898** — the card declares no `Files:` line, so it made no footprint claim to compare against
- **DRE-2911** — the card declares no `Files:` line, so it made no footprint claim to compare against; DRE-2937: this token cannot read dreadnought-foundry/agent-bureau, and an empty PR search there is indistinguishable from a card that never produced one; DRE-2936: this token cannot read dreadnought-foundry/agent-bureau, and an empty PR search there is indistinguishable from a card that never produced one; DRE-2935: this token cannot read dreadnought-foundry/agent-bureau, and an empty PR search there is indistinguishable from a card that never produced one; DRE-2934: this token cannot read dreadnought-foundry/agent-bureau, and an empty PR search there is indistinguishable from a card that never produced one
- **DRE-2912** — the card declares no `Files:` line, so it made no footprint claim to compare against; DRE-2942: this token cannot read dreadnought-foundry/agent-bureau, and an empty PR search there is indistinguishable from a card that never produced one; DRE-2941: this token cannot read dreadnought-foundry/agent-bureau, and an empty PR search there is indistinguishable from a card that never produced one; DRE-2940: this token cannot read dreadnought-foundry/agent-bureau, and an empty PR search there is indistinguishable from a card that never produced one; DRE-2939: this token cannot read dreadnought-foundry/agent-bureau, and an empty PR search there is indistinguishable from a card that never produced one
- **DRE-2937** — DRE-2953: this token cannot read dreadnought-foundry/agent-bureau, and an empty PR search there is indistinguishable from a card that never produced one; DRE-2952: this token cannot read dreadnought-foundry/agent-bureau, and an empty PR search there is indistinguishable from a card that never produced one
- **DRE-2968** — the card declares no `Files:` line, so it made no footprint claim to compare against
- **DRE-3012** — the card declares no `Files:` line, so it made no footprint claim to compare against; DRE-3025: this token cannot read dreadnought-foundry/portico, and an empty PR search there is indistinguishable from a card that never produced one; DRE-3024: this token cannot read dreadnought-foundry/portico, and an empty PR search there is indistinguishable from a card that never produced one; DRE-3023: this token cannot read dreadnought-foundry/portico, and an empty PR search there is indistinguishable from a card that never produced one
- **DRE-3022** — DRE-3078: no merged pull request this run could read
- **DRE-3037** — the card declares no `Files:` line, so it made no footprint claim to compare against
- **DRE-3213** — DRE-3238: this token cannot read dreadnought-foundry/agent-bureau, and an empty PR search there is indistinguishable from a card that never produced one
- **DRE-4297** — DRE-4298: this token cannot read dreadnought-foundry/agent-bureau, and an empty PR search there is indistinguishable from a card that never produced one
- **DRE-4322** — the card declares no `Files:` line, so it made no footprint claim to compare against; DRE-4377: this token cannot read dreadnought-foundry/agent-bureau, and an empty PR search there is indistinguishable from a card that never produced one
- **DRE-4401** — the card declares no `Files:` line, so it made no footprint claim to compare against
- **DRE-5010** — DRE-5236: this token cannot read dreadnought-foundry/portico, and an empty PR search there is indistinguishable from a card that never produced one
- **DRE-5131** — the card declares no `Files:` line, so it made no footprint claim to compare against; DRE-5140: this token cannot read dreadnought-foundry/agent-bureau, and an empty PR search there is indistinguishable from a card that never produced one
- **DRE-5280** — DRE-5284: no merged pull request this run could read
